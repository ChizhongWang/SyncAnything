from __future__ import annotations

import os
import platform
import plistlib
import shutil
import socket
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from syncanything.connections import syncanything_home


SERVER_LABEL = "com.syncanything.server"
HOTKEY_LABEL = "com.syncanything.hotkey"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 7331
DEFAULT_URL = f"http://{DEFAULT_HOST}:{DEFAULT_PORT}"
SHORTCUT_LABEL = "Control + Command + K"


@dataclass(frozen=True, slots=True)
class ShortcutPaths:
    root: Path
    source: Path
    helper: Path
    logs: Path
    server_plist: Path
    hotkey_plist: Path


def shortcut_paths(
    home: Path | None = None,
    launch_agents: Path | None = None,
) -> ShortcutPaths:
    root = (home or syncanything_home()) / "shortcut"
    agents = launch_agents or (Path.home() / "Library" / "LaunchAgents")
    return ShortcutPaths(
        root=root,
        source=root / "SyncAnythingHotkey.m",
        helper=root / "syncanything-hotkey",
        logs=root / "logs",
        server_plist=agents / f"{SERVER_LABEL}.plist",
        hotkey_plist=agents / f"{HOTKEY_LABEL}.plist",
    )


def hotkey_source(url: str = DEFAULT_URL) -> str:
    escaped_url = url.replace("\\", "\\\\").replace('"', '\\"')
    return f"""#import <Cocoa/Cocoa.h>
#import <Carbon/Carbon.h>
#import <WebKit/WebKit.h>

static const CGFloat kPanelWidth = 760.0;
static const CGFloat kCollapsedHeight = 92.0;
static const CGFloat kMaximumHeight = 590.0;

@interface SyncAnythingPanel : NSPanel
@end

@implementation SyncAnythingPanel
- (BOOL)canBecomeKeyWindow {{ return YES; }}
- (BOOL)canBecomeMainWindow {{ return NO; }}
@end

@interface SyncAnythingDelegate : NSObject <NSApplicationDelegate, WKNavigationDelegate, WKScriptMessageHandler>
@property(nonatomic, strong) SyncAnythingPanel *panel;
@property(nonatomic, strong) WKWebView *webView;
@property(nonatomic, strong) id keyMonitor;
@property(nonatomic, assign) EventHotKeyRef hotkeyRef;
@property(nonatomic, assign) BOOL hiding;
- (void)togglePanel;
@end

static OSStatus handle_hotkey(
    EventHandlerCallRef next_handler,
    EventRef event,
    void *user_data
) {{
    SyncAnythingDelegate *delegate = (__bridge SyncAnythingDelegate *)user_data;
    [delegate togglePanel];
    return noErr;
}}

@implementation SyncAnythingDelegate

- (NSScreen *)activeScreen {{
    NSPoint cursor = [NSEvent mouseLocation];
    for (NSScreen *screen in [NSScreen screens]) {{
        if (NSPointInRect(cursor, screen.frame)) return screen;
    }}
    return [NSScreen mainScreen] ?: [[NSScreen screens] firstObject];
}}

- (void)positionPanelWithHeight:(CGFloat)height {{
    NSScreen *screen = [self activeScreen];
    NSRect visible = screen.visibleFrame;
    CGFloat width = MIN(kPanelWidth, visible.size.width - 40.0);
    CGFloat top = NSMaxY(visible) - MAX(72.0, visible.size.height * 0.18);
    NSRect frame = NSMakeRect(
        NSMidX(visible) - width / 2.0,
        top - height,
        width,
        height
    );
    [self.panel setFrame:frame display:NO];
}}

- (void)resizePanelToHeight:(CGFloat)requestedHeight {{
    CGFloat height = MIN(MAX(requestedHeight, kCollapsedHeight), kMaximumHeight);
    NSRect frame = self.panel.frame;
    CGFloat top = NSMaxY(frame);
    frame.origin.y = top - height;
    frame.size.height = height;
    [self.panel setFrame:frame display:YES animate:self.panel.isVisible];
}}

- (void)hidePanel {{
    if (!self.panel.isVisible || self.hiding) return;
    self.hiding = YES;
    [self.panel orderOut:nil];
    [NSApp hide:nil];
    self.hiding = NO;
}}

- (void)showPanel {{
    [self positionPanelWithHeight:kCollapsedHeight];
    [NSApp activateIgnoringOtherApps:YES];
    [self.panel makeKeyAndOrderFront:nil];
    [self.webView evaluateJavaScript:
        @"window.syncAnythingOverlayDidShow && window.syncAnythingOverlayDidShow()"
        completionHandler:nil];
}}

- (void)togglePanel {{
    dispatch_async(dispatch_get_main_queue(), ^{{
        if (self.panel.isVisible) [self hidePanel];
        else [self showPanel];
    }});
}}

- (void)panelDidResignKey:(NSNotification *)notification {{
    [self hidePanel];
}}

- (void)webView:(WKWebView *)webView didFinishNavigation:(WKNavigation *)navigation {{
    if (self.panel.isVisible) {{
        [webView evaluateJavaScript:
            @"window.syncAnythingOverlayDidShow && window.syncAnythingOverlayDidShow()"
            completionHandler:nil];
    }}
}}

- (void)userContentController:(WKUserContentController *)controller
      didReceiveScriptMessage:(WKScriptMessage *)message {{
    if (![message.body isKindOfClass:[NSDictionary class]]) return;
    NSDictionary *payload = (NSDictionary *)message.body;
    NSString *type = payload[@"type"];
    if ([type isEqualToString:@"resize"]) {{
        NSNumber *height = payload[@"height"];
        if ([height isKindOfClass:[NSNumber class]]) {{
            [self resizePanelToHeight:height.doubleValue];
        }}
        return;
    }}
    if ([type isEqualToString:@"hide"]) {{
        [self hidePanel];
        return;
    }}
    if ([type isEqualToString:@"open"]) {{
        NSString *value = payload[@"url"];
        NSURL *target = [NSURL URLWithString:value ?: @""];
        BOOL safeHost = [target.host isEqualToString:@"127.0.0.1"];
        BOOL safePort = target.port == nil || target.port.integerValue == {DEFAULT_PORT};
        if ([target.scheme isEqualToString:@"http"] && safeHost && safePort) {{
            [[NSWorkspace sharedWorkspace] openURL:target];
            [self hidePanel];
        }}
    }}
}}

- (void)applicationDidFinishLaunching:(NSNotification *)notification {{
    WKWebViewConfiguration *configuration = [[WKWebViewConfiguration alloc] init];
    [configuration.userContentController addScriptMessageHandler:self name:@"syncanything"];
    self.webView = [[WKWebView alloc] initWithFrame:NSZeroRect configuration:configuration];
    self.webView.navigationDelegate = self;
    self.webView.autoresizingMask = NSViewWidthSizable | NSViewHeightSizable;
    self.webView.wantsLayer = YES;
    self.webView.layer.backgroundColor = NSColor.clearColor.CGColor;
    [self.webView setValue:@NO forKey:@"drawsBackground"];

    NSVisualEffectView *material = [[NSVisualEffectView alloc] initWithFrame:NSZeroRect];
    material.material = NSVisualEffectMaterialHUDWindow;
    material.blendingMode = NSVisualEffectBlendingModeBehindWindow;
    material.state = NSVisualEffectStateActive;
    material.wantsLayer = YES;
    material.layer.cornerRadius = 20.0;
    material.layer.masksToBounds = YES;
    [material addSubview:self.webView];

    self.panel = [[SyncAnythingPanel alloc]
        initWithContentRect:NSMakeRect(0, 0, kPanelWidth, kCollapsedHeight)
        styleMask:NSWindowStyleMaskBorderless | NSWindowStyleMaskFullSizeContentView
        backing:NSBackingStoreBuffered
        defer:NO];
    self.panel.contentView = material;
    self.panel.backgroundColor = NSColor.clearColor;
    self.panel.opaque = NO;
    self.panel.hasShadow = YES;
    self.panel.level = NSFloatingWindowLevel;
    self.panel.releasedWhenClosed = NO;
    self.panel.movableByWindowBackground = YES;
    self.panel.collectionBehavior =
        NSWindowCollectionBehaviorCanJoinAllSpaces |
        NSWindowCollectionBehaviorFullScreenAuxiliary |
        NSWindowCollectionBehaviorTransient;

    [[NSNotificationCenter defaultCenter]
        addObserver:self
        selector:@selector(panelDidResignKey:)
        name:NSWindowDidResignKeyNotification
        object:self.panel];

    __weak SyncAnythingDelegate *weakSelf = self;
    self.keyMonitor = [NSEvent
        addLocalMonitorForEventsMatchingMask:NSEventMaskKeyDown
        handler:^NSEvent *(NSEvent *event) {{
            if (event.keyCode == kVK_Escape) {{
                [weakSelf hidePanel];
                return nil;
            }}
            return event;
        }}];

    EventTypeSpec eventType = {{kEventClassKeyboard, kEventHotKeyPressed}};
    EventHandlerUPP handler = NewEventHandlerUPP(handle_hotkey);
    OSStatus status = InstallApplicationEventHandler(
        handler,
        1,
        &eventType,
        (__bridge void *)self,
        NULL
    );
    if (status != noErr) {{
        fprintf(stderr, "Could not install SyncAnything hotkey handler: %d\\n", status);
        [NSApp terminate:nil];
        return;
    }}

    EventHotKeyID hotkeyID = {{FOUR_CHAR_CODE('SYNC'), 1}};
    status = RegisterEventHotKey(
        kVK_ANSI_K,
        cmdKey | controlKey,
        hotkeyID,
        GetApplicationEventTarget(),
        0,
        &_hotkeyRef
    );
    if (status != noErr) {{
        fprintf(stderr, "Could not register Control + Command + K: %d\\n", status);
        [NSApp terminate:nil];
        return;
    }}

    NSString *overlayURL = [@"{escaped_url}" stringByAppendingString:@"/?overlay=1"];
    [self.webView loadRequest:[NSURLRequest requestWithURL:[NSURL URLWithString:overlayURL]]];
}}

- (void)applicationWillTerminate:(NSNotification *)notification {{
    if (self.keyMonitor) [NSEvent removeMonitor:self.keyMonitor];
    if (self.hotkeyRef) UnregisterEventHotKey(self.hotkeyRef);
    [self.webView.configuration.userContentController
        removeScriptMessageHandlerForName:@"syncanything"];
}}

@end

int main(int argc, const char *argv[]) {{
    @autoreleasepool {{
        NSApplication *application = [NSApplication sharedApplication];
        SyncAnythingDelegate *delegate = [[SyncAnythingDelegate alloc] init];
        application.delegate = delegate;
        [NSApp setActivationPolicy:NSApplicationActivationPolicyAccessory];
        [application run];
    }}
    return 0;
}}
"""


def server_launch_agent(sync_executable: Path, paths: ShortcutPaths) -> dict[str, Any]:
    return {
        "Label": SERVER_LABEL,
        "ProgramArguments": [str(sync_executable), "serve", "--no-index"],
        "RunAtLoad": True,
        "KeepAlive": True,
        "ProcessType": "Background",
        "ThrottleInterval": 5,
        "StandardOutPath": str(paths.logs / "server.log"),
        "StandardErrorPath": str(paths.logs / "server-error.log"),
    }


def hotkey_launch_agent(paths: ShortcutPaths) -> dict[str, Any]:
    return {
        "Label": HOTKEY_LABEL,
        "ProgramArguments": [str(paths.helper)],
        "RunAtLoad": True,
        "KeepAlive": True,
        "ProcessType": "Interactive",
        "LimitLoadToSessionType": "Aqua",
        "ThrottleInterval": 5,
        "StandardOutPath": str(paths.logs / "hotkey.log"),
        "StandardErrorPath": str(paths.logs / "hotkey-error.log"),
    }


def _write_plist(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".plist.tmp")
    temporary.write_bytes(plistlib.dumps(payload, fmt=plistlib.FMT_XML, sort_keys=True))
    temporary.replace(path)


def _launch_target(label: str) -> str:
    return f"gui/{os.getuid()}/{label}"


def _launchctl(*arguments: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["/bin/launchctl", *arguments],
        check=check,
        capture_output=True,
        text=True,
    )


def _service_loaded(label: str) -> bool:
    return _launchctl("print", _launch_target(label), check=False).returncode == 0


def _bootstrap_launch_agent(domain: str, path: Path, attempts: int = 5) -> None:
    """Load a freshly replaced agent after launchd finishes its bootout."""
    result: subprocess.CompletedProcess[str] | None = None
    for attempt in range(attempts):
        result = _launchctl("bootstrap", domain, str(path), check=False)
        if result.returncode == 0:
            return
        if attempt + 1 < attempts:
            time.sleep(0.25 * (attempt + 1))
    assert result is not None
    raise subprocess.CalledProcessError(
        result.returncode,
        result.args,
        output=result.stdout,
        stderr=result.stderr,
    )


def _server_reachable(host: str = DEFAULT_HOST, port: int = DEFAULT_PORT) -> bool:
    try:
        with socket.create_connection((host, port), timeout=0.2):
            return True
    except OSError:
        return False


def shortcut_status(
    home: Path | None = None,
    launch_agents: Path | None = None,
) -> dict[str, Any]:
    paths = shortcut_paths(home, launch_agents)
    supported = platform.system() == "Darwin"
    return {
        "supported": supported,
        "installed": paths.server_plist.is_file()
        and paths.hotkey_plist.is_file()
        and paths.helper.is_file(),
        "server_loaded": supported and _service_loaded(SERVER_LABEL),
        "hotkey_loaded": supported and _service_loaded(HOTKEY_LABEL),
        "server_reachable": supported and _server_reachable(),
        "shortcut": SHORTCUT_LABEL,
        "url": DEFAULT_URL,
    }


def install_shortcut(sync_executable: Path | None = None) -> dict[str, Any]:
    if platform.system() != "Darwin":
        raise RuntimeError("The global search shortcut is currently available on macOS only")

    executable = sync_executable or Path(shutil.which("syncanything") or sys.argv[0])
    executable = executable.expanduser().resolve()
    if not executable.is_file():
        raise RuntimeError(f"Could not find the SyncAnything executable: {executable}")

    compiler = shutil.which("clang")
    if not compiler:
        raise RuntimeError(
            "C compiler not found. Install Apple's Command Line Tools and try again."
        )

    paths = shortcut_paths()
    paths.root.mkdir(parents=True, exist_ok=True)
    paths.logs.mkdir(parents=True, exist_ok=True)
    module_cache = paths.root / "module-cache"
    module_cache.mkdir(parents=True, exist_ok=True)
    paths.source.write_text(hotkey_source(), encoding="utf-8")
    subprocess.run(
        [
            compiler,
            str(paths.source),
            "-o",
            str(paths.helper),
            "-framework",
            "Carbon",
            "-framework",
            "Cocoa",
            "-framework",
            "WebKit",
            "-fobjc-arc",
            f"-fmodules-cache-path={module_cache}",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    paths.helper.chmod(0o755)

    for label in (HOTKEY_LABEL, SERVER_LABEL):
        _launchctl("bootout", _launch_target(label), check=False)

    _write_plist(paths.server_plist, server_launch_agent(executable, paths))
    _write_plist(paths.hotkey_plist, hotkey_launch_agent(paths))
    domain = f"gui/{os.getuid()}"
    _bootstrap_launch_agent(domain, paths.server_plist)
    _bootstrap_launch_agent(domain, paths.hotkey_plist)
    return shortcut_status()


def uninstall_shortcut() -> dict[str, Any]:
    if platform.system() != "Darwin":
        raise RuntimeError("The global search shortcut is currently available on macOS only")
    paths = shortcut_paths()
    for label in (HOTKEY_LABEL, SERVER_LABEL):
        _launchctl("bootout", _launch_target(label), check=False)
    for path in (paths.hotkey_plist, paths.server_plist, paths.helper, paths.source):
        path.unlink(missing_ok=True)
    return shortcut_status()
