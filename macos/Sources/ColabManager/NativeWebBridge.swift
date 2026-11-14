import AppKit
import Foundation
import UserNotifications
import WebKit

final class WeakScriptMessageHandler: NSObject, WKScriptMessageHandler {
    private weak var target: WKScriptMessageHandler?

    init(target: WKScriptMessageHandler) {
        self.target = target
        super.init()
    }

    func userContentController(
        _ userContentController: WKUserContentController,
        didReceive message: WKScriptMessage
    ) {
        target?.userContentController(userContentController, didReceive: message)
    }
}

@MainActor
final class NativeWebBridge: NSObject, WKScriptMessageHandler {
    static let handlerName = "colabNative"

    private weak var webView: WKWebView?
    private weak var window: NSWindow?
    private let daemonManager: BackendDaemonManager
    private let onShowDaemonLogs: () -> Void

    private var isWebViewReady = false
    private var pendingCommands: [NativeCommandDispatch] = []
    private var notificationAuthRequested = false

    init(
        daemonManager: BackendDaemonManager,
        onShowDaemonLogs: @escaping () -> Void
    ) {
        self.daemonManager = daemonManager
        self.onShowDaemonLogs = onShowDaemonLogs
        super.init()
    }

    func attach(webView: WKWebView, window: NSWindow?) {
        self.webView = webView
        self.window = window
    }

    func markWebViewReady(_ ready: Bool) {
        isWebViewReady = ready
        if ready {
            flushPendingCommands()
        }
    }

    func sendCommand(_ command: String, payload: [String: Any]? = nil) {
        guard isWebViewReady, let webView else {
            let stringPayload = (payload ?? [:]).reduce(into: [String: String]()) { acc, entry in
                acc[entry.key] = String(describing: entry.value)
            }
            pendingCommands.append(NativeCommandDispatch(command: command, payload: stringPayload))
            return
        }
        evaluateNativeCommand(on: webView, command: command, payload: payload ?? [:])
    }

    private func flushPendingCommands() {
        guard isWebViewReady, let webView, !pendingCommands.isEmpty else { return }
        let queued = pendingCommands
        pendingCommands.removeAll()
        for item in queued {
            evaluateNativeCommand(on: webView, command: item.command, payload: item.payload)
        }
    }

    private func evaluateNativeCommand(on webView: WKWebView, command: String, payload: [String: Any]) {
        let detailObj: [String: Any] = [
            "command": command,
            "payload": payload
        ]
        guard let data = try? JSONSerialization.data(withJSONObject: detailObj, options: []),
              let jsonString = String(data: data, encoding: .utf8) else {
            return
        }
        let script = "window.dispatchEvent(new CustomEvent('colab:native-command', { detail: \(jsonString) }));"
        webView.evaluateJavaScript(script, completionHandler: nil)
    }

    // MARK: - WKScriptMessageHandler

    func userContentController(
        _ userContentController: WKUserContentController,
        didReceive message: WKScriptMessage
    ) {
        guard message.name == Self.handlerName,
              let body = message.body as? [String: Any],
              let type = body["type"] as? String else {
            return
        }

        switch type {
        case "haptic":
            let style = body["style"] as? String ?? "light"
            performHaptic(style: style)

        case "notify":
            let title = (body["title"] as? String)?.trimmingCharacters(in: .whitespacesAndNewlines) ?? "Colab Manager"
            let text = (body["body"] as? String) ?? ""
            let subtitle = body["subtitle"] as? String
            deliverNotification(title: title, body: text, subtitle: subtitle)

        case "openExternal":
            if let urlString = body["url"] as? String,
               let url = URL(string: urlString),
               let scheme = url.scheme?.lowercased(),
               ["http", "https", "mailto", "vscode", "cursor"].contains(scheme) {
                NSWorkspace.shared.open(url)
            }

        case "openTerminalSSH":
            if let port = parseInt(body["port"]), (1...65535).contains(port) {
                let sessionName = body["sessionName"] as? String
                let command = body["command"] as? String
                Self.openTerminalSSH(port: port, sessionName: sessionName, command: command)
            }

        case "openIDE":
            let ideRaw = (body["ide"] as? String)?.lowercased() ?? "cursor"
            let target = NativeIDETarget(rawValue: ideRaw) ?? .cursor
            if let port = parseInt(body["port"]), (1...65535).contains(port) {
                let remotePath = body["remotePath"] as? String ?? "/content"
                Self.openIDE(target, port: port, remotePath: remotePath)
            }

        case "revealInFinder":
            let target = (body["target"] as? String)?.lowercased() ?? "workspace"
            revealInFinder(target: target)

        case "showDaemonLogs":
            onShowDaemonLogs()

        case "windowAction":
            let action = body["action"] as? String ?? ""
            handleWindowAction(action)

        case "updateFleetSummary":
            let activeRuntimes = parseInt(body["activeRuntimes"])
            let totalCcu = parseDouble(body["totalCcu"])
            let burnRate = parseDouble(body["burnRate"])
            daemonManager.applyWebFleetSummary(
                activeRuntimes: activeRuntimes,
                totalCcu: totalCcu,
                burnRate: burnRate
            )

        default:
            break
        }
    }

    // MARK: - Native Actions (Reusable by MenuBarController & Bridge)

    func performHaptic(style: String) {
        let pattern: NSHapticFeedbackManager.FeedbackPattern
        switch style.lowercased() {
        case "medium", "generic":
            pattern = .generic
        case "success", "levelchange", "level_change":
            pattern = .levelChange
        default:
            pattern = .alignment
        }
        NSHapticFeedbackManager.defaultPerformer.perform(pattern, performanceTime: .now)
    }

    func revealInFinder(target: String) {
        daemonManager.ensureDirectoriesExist()
        switch target {
        case "data":
            NSWorkspace.shared.open(daemonManager.dataDir)
        case "logs":
            if FileManager.default.fileExists(atPath: daemonManager.daemonLogURL.path) {
                NSWorkspace.shared.activateFileViewerSelecting([daemonManager.daemonLogURL])
            } else {
                NSWorkspace.shared.open(daemonManager.logsDir)
            }
        default:
            NSWorkspace.shared.open(daemonManager.workspaceDir)
        }
    }

    static func openTerminalSSH(port: Int, sessionName: String? = nil, command: String? = nil) {
        var sshCmd = "ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -p \(port) root@localhost"
        if let extra = command?.trimmingCharacters(in: .whitespacesAndNewlines), !extra.isEmpty {
            if extra.hasPrefix("ssh ") {
                sshCmd = extra
            } else {
                let shellEscaped = extra.replacingOccurrences(of: "'", with: "'\\''")
                sshCmd = "ssh -t -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -p \(port) root@localhost '\(shellEscaped)'"
            }
        } else if let name = sessionName?.trimmingCharacters(in: .whitespacesAndNewlines), !name.isEmpty {
            let safeBanner = name.replacingOccurrences(of: "'", with: "")
            sshCmd = "printf '\\033]0;Colab: \(safeBanner) (:\(port))\\007' && \(sshCmd)"
        }

        let appleScriptEscaped = sshCmd
            .replacingOccurrences(of: "\\", with: "\\\\")
            .replacingOccurrences(of: "\"", with: "\\\"")

        let script = """
        tell application "Terminal"
            activate
            do script "\(appleScriptEscaped)"
        end tell
        """

        Task.detached(priority: .userInitiated) {
            let proc = Process()
            proc.executableURL = URL(fileURLWithPath: "/usr/bin/osascript")
            proc.arguments = ["-e", script]
            proc.standardOutput = Pipe()
            proc.standardError = Pipe()
            try? proc.run()
            proc.waitUntilExit()
        }
    }

    static func openIDE(_ ide: NativeIDETarget, port: Int, remotePath: String = "/content") {
        let hostAlias = "colab-local-\(port)"
        try? upsertSSHConfigBlock(hostAlias: hostAlias, port: port)

        let normalizedPath: String
        let trimmed = remotePath.trimmingCharacters(in: .whitespacesAndNewlines)
        if trimmed.isEmpty {
            normalizedPath = "/content"
        } else if trimmed.hasPrefix("/") {
            normalizedPath = trimmed
        } else {
            normalizedPath = "/\(trimmed)"
        }

        let encodedPath = normalizedPath.addingPercentEncoding(withAllowedCharacters: .urlPathAllowed) ?? "/content"
        let uriString = "\(ide.scheme)://vscode-remote/ssh-remote+\(hostAlias)\(encodedPath)"
        if let url = URL(string: uriString) {
            let opened = NSWorkspace.shared.open(url)
            if !opened {
                // Fallback direct host:port URI if needed
                let fallbackString = "\(ide.scheme)://vscode-remote/ssh-remote+root@localhost:\(port)\(encodedPath)"
                if let fallbackURL = URL(string: fallbackString) {
                    NSWorkspace.shared.open(fallbackURL)
                }
            }
        }
    }

    static func upsertSSHConfigBlock(hostAlias: String, port: Int) throws {
        let fm = FileManager.default
        let sshDir = fm.homeDirectoryForCurrentUser.appendingPathComponent(".ssh", isDirectory: true)
        if !fm.fileExists(atPath: sshDir.path) {
            try fm.createDirectory(at: sshDir, withIntermediateDirectories: true, attributes: [.posixPermissions: 0o700])
        } else {
            try? fm.setAttributes([.posixPermissions: 0o700], ofItemAtPath: sshDir.path)
        }

        let configURL = sshDir.appendingPathComponent("config")
        let beginMarker = "# BEGIN COLAB-MANAGER \(hostAlias)"
        let endMarker = "# END COLAB-MANAGER \(hostAlias)"

        let block = """
        \(beginMarker)
        Host \(hostAlias)
            HostName 127.0.0.1
            Port \(port)
            User root
            StrictHostKeyChecking no
            UserKnownHostsFile /dev/null
            ServerAliveInterval 30
            ServerAliveCountMax 3
        \(endMarker)
        """

        var existing = (try? String(contentsOf: configURL, encoding: .utf8)) ?? ""
        if let startRange = existing.range(of: beginMarker),
           let endRange = existing.range(of: endMarker, range: startRange.lowerBound..<existing.endIndex) {
            let replaceRange = startRange.lowerBound..<endRange.upperBound
            existing.replaceSubrange(replaceRange, with: block)
        } else {
            if !existing.isEmpty && !existing.hasSuffix("\n") {
                existing.append("\n")
            }
            if !existing.isEmpty {
                existing.append("\n")
            }
            existing.append(block)
            existing.append("\n")
        }

        try existing.write(to: configURL, atomically: true, encoding: .utf8)
        try? fm.setAttributes([.posixPermissions: 0o600], ofItemAtPath: configURL.path)
    }

    // MARK: - Private Helpers

    private func handleWindowAction(_ action: String) {
        guard let targetWindow = window ?? webView?.window else { return }
        switch action {
        case "startDrag":
            let dragEvent: NSEvent?
            if let event = NSApp.currentEvent,
               event.type == .leftMouseDown || event.type == .leftMouseDragged {
                dragEvent = event
            } else {
                dragEvent = (webView as? WorkbenchWebView)?.recentMouseDownEvent()
            }
            if let dragEvent {
                targetWindow.performDrag(with: dragEvent)
            }
        case "zoom":
            targetWindow.performZoom(nil)
        case "minimize":
            targetWindow.miniaturize(nil)
        case "close":
            targetWindow.performClose(nil)
        case "toggleFullScreen":
            targetWindow.toggleFullScreen(nil)
        default:
            break
        }
    }

    private func deliverNotification(title: String, body: String, subtitle: String?) {
        guard Self.canUseUserNotificationCenter else { return }
        let center = UNUserNotificationCenter.current()
        if !notificationAuthRequested {
            notificationAuthRequested = true
            center.requestAuthorization(options: [.alert, .sound, .badge]) { _, _ in }
        }

        let content = UNMutableNotificationContent()
        content.title = title
        content.body = body
        if let subtitle, !subtitle.isEmpty {
            content.subtitle = subtitle
        }
        content.sound = .default

        let request = UNNotificationRequest(
            identifier: "colab-native-\(UUID().uuidString)",
            content: content,
            trigger: nil
        )
        center.add(request, withCompletionHandler: nil)
    }

    static var canUseUserNotificationCenter: Bool {
        Bundle.main.bundleIdentifier != nil && Bundle.main.bundleURL.pathExtension == "app"
    }

    private func parseInt(_ value: Any?) -> Int? {
        if let i = value as? Int { return i }
        if let d = value as? Double { return Int(d) }
        if let n = value as? NSNumber { return n.intValue }
        if let s = value as? String { return Int(s.trimmingCharacters(in: .whitespacesAndNewlines)) }
        return nil
    }

    private func parseDouble(_ value: Any?) -> Double? {
        if let d = value as? Double { return d }
        if let i = value as? Int { return Double(i) }
        if let n = value as? NSNumber { return n.doubleValue }
        if let s = value as? String { return Double(s.trimmingCharacters(in: .whitespacesAndNewlines)) }
        return nil
    }
}
