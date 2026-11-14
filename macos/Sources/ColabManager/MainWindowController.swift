import AppKit
import Foundation

@MainActor
final class MainWindowController: NSWindowController, NSWindowDelegate {
    private let containerView: WorkbenchContainerView
    private let bridge: NativeWebBridge

    init(
        daemonManager: BackendDaemonManager,
        bridge: NativeWebBridge,
        onShowDaemonLogs: @escaping () -> Void
    ) {
        self.bridge = bridge

        let initialRect = NSRect(x: 0, y: 0, width: 1320, height: 840)
        let window = NSWindow(
            contentRect: initialRect,
            styleMask: [.titled, .closable, .miniaturizable, .resizable, .fullSizeContentView],
            backing: .buffered,
            defer: false
        )
        window.title = "Colab Manager"
        window.minSize = NSSize(width: 980, height: 640)
        window.titlebarAppearsTransparent = true
        window.titleVisibility = .hidden
        window.appearance = NSAppearance(named: .darkAqua)
        window.backgroundColor = .clear
        window.isOpaque = false
        window.isMovableByWindowBackground = false
        window.isReleasedWhenClosed = false
        window.collectionBehavior = [.fullScreenPrimary, .managed]

        if !window.setFrameUsingName("ColabManagerMainWindow") {
            window.center()
        }
        window.setFrameAutosaveName("ColabManagerMainWindow")

        self.containerView = WorkbenchContainerView(
            frame: initialRect,
            daemonManager: daemonManager,
            bridge: bridge,
            onShowDaemonLogs: onShowDaemonLogs
        )

        super.init(window: window)

        window.delegate = self
        window.contentView = containerView
        bridge.attach(webView: containerView.webView, window: window)
    }

    @available(*, unavailable)
    required init?(coder: NSCoder) {
        fatalError("init(coder:) has not been implemented")
    }

    func showAndFocus() {
        guard let window else { return }
        if !window.isVisible {
            window.makeKeyAndOrderFront(nil)
        } else {
            window.orderFrontRegardless()
            window.makeKey()
        }
        NSApp.activate(ignoringOtherApps: true)
    }

    func reloadWorkbench() {
        containerView.reloadWorkbench()
    }

    func dispatchNativeCommand(_ command: String, payload: [String: Any]? = nil, focusWindow: Bool = true) {
        if focusWindow {
            showAndFocus()
        }
        bridge.sendCommand(command, payload: payload)
    }

    func windowDidBecomeKey(_ notification: Notification) {
        bridge.attach(webView: containerView.webView, window: window)
    }
}
