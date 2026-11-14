import AppKit
import Combine
import SwiftUI

@MainActor
final class MenuBarController: NSObject {
    private let statusItem: NSStatusItem
    private let popover: NSPopover
    private let daemonManager: BackendDaemonManager
    private let onOpenMainWindow: () -> Void
    private let onSendCommand: (String, [String: Any]?, Bool) -> Void
    private let onRevealWorkspace: () -> Void
    private let onShowDaemonLogs: () -> Void

    private var cancellables: Set<AnyCancellable> = []

    init(
        daemonManager: BackendDaemonManager,
        onOpenMainWindow: @escaping () -> Void,
        onSendCommand: @escaping (String, [String: Any]?, Bool) -> Void,
        onRevealWorkspace: @escaping () -> Void,
        onShowDaemonLogs: @escaping () -> Void
    ) {
        self.daemonManager = daemonManager
        self.onOpenMainWindow = onOpenMainWindow
        self.onSendCommand = onSendCommand
        self.onRevealWorkspace = onRevealWorkspace
        self.onShowDaemonLogs = onShowDaemonLogs

        self.statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        self.popover = NSPopover()

        super.init()

        configureStatusItem()
        configurePopover()
        observeDaemonSummary()
    }

    private func configureStatusItem() {
        guard let button = statusItem.button else { return }
        button.target = self
        button.action = #selector(togglePopover(_:))
        button.imagePosition = .imageLeading
        button.font = NSFont.monospacedDigitSystemFont(ofSize: 11.5, weight: .medium)
        updateStatusButton(
            healthy: daemonManager.daemonHealthy,
            starting: daemonManager.isStarting,
            activeCount: daemonManager.effectiveActiveRuntimeCount,
            totalCcu: daemonManager.totalCcu
        )
    }

    private func configurePopover() {
        popover.behavior = .transient
        popover.animates = true
        popover.appearance = NSAppearance(named: .darkAqua)
        popover.contentSize = NSSize(width: 340, height: 440)

        let rootView = MenuBarPopoverView(
            daemonManager: daemonManager,
            onOpenMainWindow: { [weak self] in
                self?.closePopover()
                self?.onOpenMainWindow()
            },
            onSendCommand: { [weak self] command, payload in
                self?.closePopover()
                self?.onSendCommand(command, payload, true)
            },
            onSyncQuotas: { [weak self] in
                guard let self else { return }
                Task { @MainActor in
                    _ = await self.daemonManager.syncAllAccounts()
                    self.onSendCommand("sync-accounts", nil, false)
                }
            },
            onRevealWorkspace: { [weak self] in
                self?.closePopover()
                self?.onRevealWorkspace()
            },
            onShowDaemonLogs: { [weak self] in
                self?.closePopover()
                self?.onShowDaemonLogs()
            },
            onOpenTerminalSSH: { [weak self] session in
                guard let port = session.ssh_local_port else { return }
                self?.closePopover()
                NativeWebBridge.openTerminalSSH(port: port, sessionName: session.name)
            },
            onOpenIDE: { [weak self] ide, session in
                guard let port = session.ssh_local_port else { return }
                self?.closePopover()
                NativeWebBridge.openIDE(ide, port: port, remotePath: session.remote_workdir)
            },
            onQuit: {
                NSApp.terminate(nil)
            }
        )
        popover.contentViewController = NSHostingController(rootView: rootView)
    }

    private func observeDaemonSummary() {
        daemonManager.$daemonHealthy
            .combineLatest(
                daemonManager.$isStarting,
                daemonManager.$activeSessions,
                daemonManager.$totalCcu
            )
            .combineLatest(daemonManager.$webReportedActiveRuntimes)
            .receive(on: RunLoop.main)
            .sink { [weak self] tuple, _ in
                guard let self else { return }
                let (healthy, starting, _, totalCcu) = tuple
                self.updateStatusButton(
                    healthy: healthy,
                    starting: starting,
                    activeCount: self.daemonManager.effectiveActiveRuntimeCount,
                    totalCcu: totalCcu
                )
            }
            .store(in: &cancellables)
    }

    private func updateStatusButton(healthy: Bool, starting: Bool, activeCount: Int, totalCcu: Double) {
        guard let button = statusItem.button else { return }

        let symbolName = activeCount > 0 ? "bolt.horizontal.circle.fill" : "cpu.fill"
        if let image = NSImage(systemSymbolName: symbolName, accessibilityDescription: "Colab Manager") {
            let config = NSImage.SymbolConfiguration(pointSize: 13, weight: .medium)
            let configured = image.withSymbolConfiguration(config) ?? image
            configured.isTemplate = true
            button.image = configured
        }

        let badge: String
        if !healthy {
            badge = starting ? " Booting" : " Offline"
        } else if activeCount > 0 {
            if totalCcu > 0 {
                badge = String(format: " %d GPU · %.1f CCU", activeCount, totalCcu)
            } else {
                badge = " \(activeCount) GPU"
            }
        } else if totalCcu > 0 {
            badge = String(format: " %.1f CCU", totalCcu)
        } else {
            badge = " 0 GPU"
        }

        button.title = badge
        button.toolTip = healthy
            ? "Colab Manager — :1113 online (\(activeCount) active GPU)"
            : "Colab Manager — daemon offline"
    }

    @objc private func togglePopover(_ sender: Any?) {
        guard let button = statusItem.button else { return }
        if popover.isShown {
            popover.performClose(sender)
        } else {
            Task { @MainActor in
                await daemonManager.refreshFleetNow()
            }
            popover.show(relativeTo: button.bounds, of: button, preferredEdge: .minY)
            popover.contentViewController?.view.window?.makeKey()
        }
    }

    private func closePopover() {
        if popover.isShown {
            popover.performClose(nil)
        }
    }
}
