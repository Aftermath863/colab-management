import AppKit
import SwiftUI

struct MenuBarPopoverView: View {
    @ObservedObject var daemonManager: BackendDaemonManager
    let onOpenMainWindow: () -> Void
    let onSendCommand: (String, [String: Any]?) -> Void
    let onSyncQuotas: () -> Void
    let onRevealWorkspace: () -> Void
    let onShowDaemonLogs: () -> Void
    let onOpenTerminalSSH: (SessionSummary) -> Void
    let onOpenIDE: (NativeIDETarget, SessionSummary) -> Void
    let onQuit: () -> Void

    @State private var copiedSessionId: String? = nil

    var body: some View {
        ZStack {
            LinearGradient(
                colors: [
                    Color(red: 0.04, green: 0.06, blue: 0.12).opacity(0.86),
                    Color(red: 0.03, green: 0.04, blue: 0.09).opacity(0.92)
                ],
                startPoint: .topLeading,
                endPoint: .bottomTrailing
            )
            .ignoresSafeArea()

            VStack(spacing: 11) {
                headerBar
                telemetryRow
                runtimesSection
                quickActionsGrid
                footerBar
            }
            .padding(13)
        }
        .frame(width: 340, height: 440)
        .background(.ultraThinMaterial)
        .preferredColorScheme(.dark)
    }

    // MARK: - Header

    private var headerBar: some View {
        HStack(spacing: 8) {
            ZStack {
                RoundedRectangle(cornerRadius: 8, style: .continuous)
                    .fill(
                        LinearGradient(
                            colors: [
                                Color(red: 0.24, green: 0.49, blue: 0.96),
                                Color(red: 0.36, green: 0.32, blue: 0.88)
                            ],
                            startPoint: .topLeading,
                            endPoint: .bottomTrailing
                        )
                    )
                    .frame(width: 26, height: 26)
                Image(systemName: "cpu.fill")
                    .font(.system(size: 13, weight: .semibold))
                    .foregroundStyle(.white)
            }

            VStack(alignment: .leading, spacing: 1) {
                HStack(spacing: 5) {
                    Text("Colab Manager")
                        .font(.system(size: 12.5, weight: .semibold))
                        .foregroundStyle(.white)
                    Text("arm64")
                        .font(.system(size: 9, weight: .semibold, design: .monospaced))
                        .padding(.horizontal, 4.5)
                        .padding(.vertical, 1)
                        .background(Color.white.opacity(0.09), in: Capsule())
                        .foregroundStyle(Color.white.opacity(0.72))
                }
                Text("SSH Pool 2220–2229")
                    .font(.system(size: 10, design: .monospaced))
                    .foregroundStyle(Color.white.opacity(0.48))
            }

            Spacer()

            HStack(spacing: 5) {
                Circle()
                    .fill(healthColor)
                    .frame(width: 6.5, height: 6.5)
                    .shadow(color: healthColor.opacity(0.8), radius: 4)
                Text(":1113")
                    .font(.system(size: 10.5, weight: .semibold, design: .monospaced))
                    .foregroundStyle(.white.opacity(0.88))
            }
            .padding(.horizontal, 8)
            .padding(.vertical, 4)
            .background(
                Capsule()
                    .fill(Color.white.opacity(0.06))
                    .overlay(
                        Capsule()
                            .stroke(healthColor.opacity(0.35), lineWidth: 1)
                    )
            )
        }
    }

    private var healthColor: Color {
        if daemonManager.daemonHealthy { return Color(red: 0.20, green: 0.83, blue: 0.60) }
        if daemonManager.isStarting || daemonManager.isRestarting { return .orange }
        return Color(red: 0.96, green: 0.34, blue: 0.36)
    }

    // MARK: - Telemetry Bento Cards

    private var telemetryRow: some View {
        HStack(spacing: 8) {
            liquidGlassCard {
                VStack(alignment: .leading, spacing: 4) {
                    HStack {
                        Text("POOL CCU BALANCE")
                            .font(.system(size: 9, weight: .semibold))
                            .tracking(0.5)
                            .foregroundStyle(Color.white.opacity(0.52))
                        Spacer()
                        Image(systemName: "bolt.fill")
                            .font(.system(size: 9.5))
                            .foregroundStyle(Color.cyan.opacity(0.85))
                    }
                    HStack(alignment: .firstTextBaseline, spacing: 4) {
                        Text(String(format: "%.1f", daemonManager.totalCcu))
                            .font(.system(size: 20, weight: .bold, design: .rounded))
                            .monospacedDigit()
                            .foregroundStyle(.white)
                        Text("CCU")
                            .font(.system(size: 10, weight: .medium, design: .monospaced))
                            .foregroundStyle(Color.cyan.opacity(0.85))
                    }
                    Text("\(daemonManager.accounts.count) linked account\(daemonManager.accounts.count == 1 ? "" : "s")")
                        .font(.system(size: 10))
                        .foregroundStyle(Color.white.opacity(0.45))
                }
            }

            liquidGlassCard {
                VStack(alignment: .leading, spacing: 4) {
                    HStack {
                        Text("BURN RATE")
                            .font(.system(size: 9, weight: .semibold))
                            .tracking(0.5)
                            .foregroundStyle(Color.white.opacity(0.52))
                        Spacer()
                        Image(systemName: "flame.fill")
                            .font(.system(size: 9.5))
                            .foregroundStyle(daemonManager.burnRate > 0 ? Color.orange : Color.white.opacity(0.35))
                    }
                    HStack(alignment: .firstTextBaseline, spacing: 3) {
                        Text(String(format: "%.2f", daemonManager.burnRate))
                            .font(.system(size: 20, weight: .bold, design: .rounded))
                            .monospacedDigit()
                            .foregroundStyle(.white)
                        Text("/hr")
                            .font(.system(size: 10, weight: .medium, design: .monospaced))
                            .foregroundStyle(Color.white.opacity(0.55))
                    }
                    let count = daemonManager.effectiveActiveRuntimeCount
                    Text(count > 0 ? "\(count) GPU runtime\(count == 1 ? "" : "s") active" : "Fleet idle")
                        .font(.system(size: 10))
                        .foregroundStyle(count > 0 ? Color(red: 0.45, green: 0.85, blue: 0.72) : Color.white.opacity(0.45))
                }
            }
        }
    }

    // MARK: - Active Runtimes List

    private var runtimesSection: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack {
                Text("ACTIVE COLAB RUNTIMES")
                    .font(.system(size: 9.5, weight: .semibold))
                    .tracking(0.6)
                    .foregroundStyle(Color.white.opacity(0.52))
                Spacer()
                Text("\(daemonManager.activeSessions.count)")
                    .font(.system(size: 10, weight: .bold, design: .monospaced))
                    .monospacedDigit()
                    .padding(.horizontal, 6)
                    .padding(.vertical, 1.5)
                    .background(Color.blue.opacity(0.22), in: Capsule())
                    .foregroundStyle(Color(red: 0.58, green: 0.78, blue: 1.0))
            }

            if daemonManager.activeSessions.isEmpty {
                liquidGlassCard {
                    HStack(spacing: 10) {
                        Image(systemName: "sparkles")
                            .font(.system(size: 15))
                            .foregroundStyle(Color.blue.opacity(0.8))
                            .frame(width: 28, height: 28)
                            .background(Color.blue.opacity(0.12), in: RoundedRectangle(cornerRadius: 8))

                        VStack(alignment: .leading, spacing: 2) {
                            Text("No active Colab runtimes")
                                .font(.system(size: 11.5, weight: .medium))
                                .foregroundStyle(.white.opacity(0.86))
                            Text("Provision a GPU/TPU instance with ⌘N")
                                .font(.system(size: 10))
                                .foregroundStyle(Color.white.opacity(0.45))
                        }
                        Spacer()
                        Button {
                            onSendCommand("new-session", nil)
                        } label: {
                            Text("Launch")
                                .font(.system(size: 10.5, weight: .semibold))
                                .padding(.horizontal, 9)
                                .padding(.vertical, 4)
                        }
                        .buttonStyle(.plain)
                        .background(Color.blue.opacity(0.28), in: RoundedRectangle(cornerRadius: 7))
                        .overlay(
                            RoundedRectangle(cornerRadius: 7)
                                .stroke(Color.blue.opacity(0.45), lineWidth: 1)
                        )
                        .foregroundStyle(.white)
                    }
                    .frame(maxWidth: .infinity)
                    .padding(.vertical, 4)
                }
                .frame(maxHeight: .infinity)
            } else {
                ScrollView(.vertical, showsIndicators: true) {
                    LazyVStack(spacing: 6) {
                        ForEach(daemonManager.activeSessions) { session in
                            runtimeRow(session)
                        }
                    }
                }
                .frame(maxHeight: .infinity)
            }
        }
    }

    private func runtimeRow(_ session: SessionSummary) -> some View {
        HStack(spacing: 8) {
            Circle()
                .fill(statusColor(for: session.status))
                .frame(width: 7, height: 7)
                .shadow(color: statusColor(for: session.status).opacity(0.75), radius: 3)

            VStack(alignment: .leading, spacing: 2) {
                HStack(spacing: 5) {
                    Text(session.name)
                        .font(.system(size: 11.5, weight: .semibold))
                        .foregroundStyle(.white)
                        .lineLimit(1)

                    Text(session.accelerator.uppercased())
                        .font(.system(size: 9, weight: .bold, design: .monospaced))
                        .padding(.horizontal, 4.5)
                        .padding(.vertical, 1)
                        .background(acceleratorColor(session.accelerator).opacity(0.20), in: RoundedRectangle(cornerRadius: 4))
                        .foregroundStyle(acceleratorColor(session.accelerator))
                }

                HStack(spacing: 5) {
                    if let port = session.ssh_local_port {
                        Text("localhost:\(port)")
                            .font(.system(size: 9.5, design: .monospaced))
                            .foregroundStyle(Color.white.opacity(0.55))
                    } else {
                        Text(session.status)
                            .font(.system(size: 9.5, design: .monospaced))
                            .foregroundStyle(Color.white.opacity(0.50))
                    }
                    if let email = session.account_email {
                        Text("• \(email)")
                            .font(.system(size: 9.5))
                            .foregroundStyle(Color.white.opacity(0.36))
                            .lineLimit(1)
                    }
                }
            }

            Spacer(minLength: 4)

            HStack(spacing: 4) {
                if let sshCmd = session.sshCommand {
                    Button {
                        NSPasteboard.general.clearContents()
                        NSPasteboard.general.setString(sshCmd, forType: .string)
                        NSHapticFeedbackManager.defaultPerformer.perform(.alignment, performanceTime: .now)
                        copiedSessionId = session.id
                        DispatchQueue.main.asyncAfter(deadline: .now() + 1.4) {
                            if copiedSessionId == session.id {
                                copiedSessionId = nil
                            }
                        }
                    } label: {
                        Image(systemName: copiedSessionId == session.id ? "checkmark" : "doc.on.doc")
                            .font(.system(size: 10.5, weight: .medium))
                            .foregroundStyle(copiedSessionId == session.id ? Color.green : Color.white.opacity(0.82))
                            .frame(width: 22, height: 22)
                            .background(Color.white.opacity(0.07), in: RoundedRectangle(cornerRadius: 6))
                    }
                    .buttonStyle(.plain)
                    .help("Copy SSH Command")

                    Button {
                        onOpenTerminalSSH(session)
                    } label: {
                        Image(systemName: "terminal.fill")
                            .font(.system(size: 10.5, weight: .medium))
                            .foregroundStyle(Color.white.opacity(0.85))
                            .frame(width: 22, height: 22)
                            .background(Color.white.opacity(0.07), in: RoundedRectangle(cornerRadius: 6))
                    }
                    .buttonStyle(.plain)
                    .help("Open in Terminal.app")

                    Menu {
                        Button("Open in Cursor") {
                            onOpenIDE(.cursor, session)
                        }
                        Button("Open in VS Code") {
                            onOpenIDE(.vscode, session)
                        }
                    } label: {
                        Image(systemName: "chevron.left.forwardslash.chevron.right")
                            .font(.system(size: 10, weight: .semibold))
                            .foregroundStyle(Color.cyan.opacity(0.9))
                            .frame(width: 22, height: 22)
                            .background(Color.white.opacity(0.07), in: RoundedRectangle(cornerRadius: 6))
                    }
                    .menuStyle(.borderlessButton)
                    .menuIndicator(.hidden)
                    .fixedSize()
                    .help("Open in Cursor or VS Code")
                }

                Button {
                    onSendCommand("open-session", ["sessionId": session.id, "id": session.id])
                } label: {
                    Image(systemName: "arrow.up.right.square")
                        .font(.system(size: 10.5, weight: .medium))
                        .foregroundStyle(Color(red: 0.56, green: 0.78, blue: 1.0))
                        .frame(width: 22, height: 22)
                        .background(Color.blue.opacity(0.18), in: RoundedRectangle(cornerRadius: 6))
                }
                .buttonStyle(.plain)
                .help("Open Workbench")
            }
        }
        .padding(.horizontal, 9)
        .padding(.vertical, 7)
        .background(
            RoundedRectangle(cornerRadius: 10, style: .continuous)
                .fill(Color.white.opacity(0.05))
                .overlay(
                    RoundedRectangle(cornerRadius: 10, style: .continuous)
                        .stroke(Color.white.opacity(0.09), lineWidth: 1)
                )
        )
    }

    // MARK: - Quick Actions

    private var quickActionsGrid: some View {
        let columns = [
            GridItem(.flexible(), spacing: 6),
            GridItem(.flexible(), spacing: 6),
            GridItem(.flexible(), spacing: 6)
        ]

        return LazyVGrid(columns: columns, spacing: 6) {
            quickActionButton(title: "New Runtime", shortcut: "⌘N", icon: "plus.circle.fill", tint: .blue) {
                onSendCommand("new-session", nil)
            }
            quickActionButton(title: "Palette", shortcut: "⌘K", icon: "command", tint: .indigo) {
                onSendCommand("command-palette", nil)
            }
            quickActionButton(
                title: daemonManager.isSyncingAccounts ? "Syncing..." : "Sync Quotas",
                shortcut: "⇧⌘R",
                icon: "arrow.triangle.2.circlepath",
                tint: .cyan
            ) {
                onSyncQuotas()
            }
            quickActionButton(title: "Workspace", shortcut: nil, icon: "folder.fill", tint: .teal) {
                onRevealWorkspace()
            }
            quickActionButton(title: "Daemon Logs", shortcut: "⌘L", icon: "doc.text.magnifyingglass", tint: .orange) {
                onShowDaemonLogs()
            }
            quickActionButton(title: "Workbench", shortcut: nil, icon: "macwindow", tint: .purple) {
                onOpenMainWindow()
            }
        }
    }

    private func quickActionButton(
        title: String,
        shortcut: String?,
        icon: String,
        tint: Color,
        action: @escaping () -> Void
    ) -> some View {
        Button(action: action) {
            VStack(alignment: .leading, spacing: 4) {
                HStack {
                    Image(systemName: icon)
                        .font(.system(size: 12, weight: .semibold))
                        .foregroundStyle(tint)
                    Spacer()
                    if let shortcut {
                        Text(shortcut)
                            .font(.system(size: 9, weight: .medium, design: .monospaced))
                            .foregroundStyle(Color.white.opacity(0.40))
                    }
                }
                Text(title)
                    .font(.system(size: 10.5, weight: .medium))
                    .foregroundStyle(.white.opacity(0.88))
                    .lineLimit(1)
            }
            .padding(.horizontal, 8)
            .padding(.vertical, 7)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(
                RoundedRectangle(cornerRadius: 9, style: .continuous)
                    .fill(Color.white.opacity(0.05))
                    .overlay(
                        RoundedRectangle(cornerRadius: 9, style: .continuous)
                            .stroke(Color.white.opacity(0.09), lineWidth: 1)
                    )
            )
        }
        .buttonStyle(.plain)
    }

    // MARK: - Footer

    private var footerBar: some View {
        HStack {
            Text(daemonManager.daemonManaged ? "Managed Daemon (:1113)" : "External Daemon (:1113)")
                .font(.system(size: 10))
                .foregroundStyle(Color.white.opacity(0.44))

            Spacer()

            Button(action: onQuit) {
                HStack(spacing: 4) {
                    Image(systemName: "power")
                        .font(.system(size: 10, weight: .semibold))
                    Text("Quit")
                        .font(.system(size: 10.5, weight: .medium))
                }
                .foregroundStyle(Color.white.opacity(0.75))
                .padding(.horizontal, 8)
                .padding(.vertical, 3.5)
                .background(Color.white.opacity(0.06), in: RoundedRectangle(cornerRadius: 6))
            }
            .buttonStyle(.plain)
        }
    }

    // MARK: - Helpers

    @ViewBuilder
    private func liquidGlassCard<Content: View>(@ViewBuilder content: () -> Content) -> some View {
        content()
            .padding(9)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(
                RoundedRectangle(cornerRadius: 11, style: .continuous)
                    .fill(Color.white.opacity(0.055))
                    .overlay(
                        RoundedRectangle(cornerRadius: 11, style: .continuous)
                            .stroke(
                                LinearGradient(
                                    colors: [Color.white.opacity(0.16), Color.white.opacity(0.05)],
                                    startPoint: .top,
                                    endPoint: .bottom
                                ),
                                lineWidth: 1
                            )
                    )
            )
    }

    private func statusColor(for status: String) -> Color {
        switch status.uppercased() {
        case "READY", "BUSY":
            return Color(red: 0.20, green: 0.85, blue: 0.60)
        case "PROVISIONING", "BOOTSTRAPPING":
            return .orange
        case "ORPHAN":
            return .yellow
        default:
            return .gray
        }
    }

    private func acceleratorColor(_ accel: String) -> Color {
        switch accel.uppercased() {
        case "H100", "A100":
            return Color(red: 0.78, green: 0.56, blue: 1.0)
        case "L4", "G4", "T4":
            return Color(red: 0.42, green: 0.78, blue: 1.0)
        case "V5E1", "V6E1":
            return Color(red: 0.98, green: 0.72, blue: 0.30)
        default:
            return Color.white.opacity(0.70)
        }
    }
}
