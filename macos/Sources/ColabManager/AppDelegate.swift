import AppKit
import UserNotifications

@MainActor
final class AppDelegate: NSObject, NSApplicationDelegate, AppMenuActions {
    private let daemonManager = BackendDaemonManager()
    private var bridge: NativeWebBridge!
    private var mainWindowController: MainWindowController!
    private var menuBarController: MenuBarController!
    private var daemonLogsWindowController: DaemonLogsWindowController!

    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.appearance = NSAppearance(named: .darkAqua)
        NSApp.mainMenu = AppMenuBuilder.buildMainMenu(target: self)

        daemonLogsWindowController = DaemonLogsWindowController(daemonManager: daemonManager)

        bridge = NativeWebBridge(
            daemonManager: daemonManager,
            onShowDaemonLogs: { [weak self] in
                self?.daemonLogsWindowController.showAndFocus()
            }
        )

        mainWindowController = MainWindowController(
            daemonManager: daemonManager,
            bridge: bridge,
            onShowDaemonLogs: { [weak self] in
                self?.daemonLogsWindowController.showAndFocus()
            }
        )

        menuBarController = MenuBarController(
            daemonManager: daemonManager,
            onOpenMainWindow: { [weak self] in
                self?.mainWindowController.showAndFocus()
            },
            onSendCommand: { [weak self] command, payload, focus in
                if focus {
                    self?.mainWindowController.dispatchNativeCommand(command, payload: payload, focusWindow: true)
                } else if self?.mainWindowController.window?.isVisible == true {
                    self?.mainWindowController.dispatchNativeCommand(command, payload: payload, focusWindow: false)
                }
            },
            onRevealWorkspace: { [weak self] in
                self?.bridge.revealInFinder(target: "workspace")
            },
            onShowDaemonLogs: { [weak self] in
                self?.daemonLogsWindowController.showAndFocus()
            }
        )

        mainWindowController.showAndFocus()

        if NativeWebBridge.canUseUserNotificationCenter {
            UNUserNotificationCenter.current().requestAuthorization(options: [.alert, .sound, .badge]) { _, _ in }
        }

        Task { @MainActor in
            await daemonManager.start()
        }
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool {
        false
    }

    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool {
        if !flag {
            mainWindowController.showAndFocus()
        }
        return true
    }

    func applicationWillTerminate(_ notification: Notification) {
        daemonManager.terminateIfManaged()
    }

    // MARK: - Menu Actions

    @objc func showMainWindowAction(_ sender: Any?) {
        mainWindowController.showAndFocus()
    }

    @objc func showDaemonLogsAction(_ sender: Any?) {
        daemonLogsWindowController.showAndFocus()
    }

    @objc func revealWorkspaceAction(_ sender: Any?) {
        bridge.revealInFinder(target: "workspace")
    }

    @objc func revealDataDirAction(_ sender: Any?) {
        bridge.revealInFinder(target: "data")
    }

    @objc func reloadWorkbenchAction(_ sender: Any?) {
        mainWindowController.showAndFocus()
        mainWindowController.reloadWorkbench()
    }

    @objc func fleetCommandPaletteAction(_ sender: Any?) {
        mainWindowController.dispatchNativeCommand("command-palette")
    }

    @objc func fleetNewSessionAction(_ sender: Any?) {
        mainWindowController.dispatchNativeCommand("new-session")
    }

    @objc func fleetLinkAccountAction(_ sender: Any?) {
        mainWindowController.dispatchNativeCommand("link-account")
    }

    @objc func fleetSyncAccountsAction(_ sender: Any?) {
        mainWindowController.dispatchNativeCommand("sync-accounts")
    }

    @objc func navigateOverviewAction(_ sender: Any?) {
        mainWindowController.dispatchNativeCommand("navigate", payload: ["page": "overview"])
    }

    @objc func navigateSessionsAction(_ sender: Any?) {
        mainWindowController.dispatchNativeCommand("navigate", payload: ["page": "sessions"])
    }

    @objc func navigateJobsAction(_ sender: Any?) {
        mainWindowController.dispatchNativeCommand("navigate", payload: ["page": "jobs"])
    }

    @objc func navigateAccountsAction(_ sender: Any?) {
        mainWindowController.dispatchNativeCommand("navigate", payload: ["page": "accounts"])
    }

    @objc func navigateTemplatesAction(_ sender: Any?) {
        mainWindowController.dispatchNativeCommand("navigate", payload: ["page": "templates"])
    }

    @objc func navigateNotificationsAction(_ sender: Any?) {
        mainWindowController.dispatchNativeCommand("navigate", payload: ["page": "notifications"])
    }
}
