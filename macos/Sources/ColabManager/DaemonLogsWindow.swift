import AppKit
import Combine
import SwiftUI

@MainActor
final class DaemonLogStreamModel: ObservableObject {
    @Published private(set) var lines: [LogLine] = []
    @Published private(set) var byteCount: UInt64 = 0
    @Published var filterText: String = ""
    @Published var autoScroll: Bool = true

    struct LogLine: Identifiable, Equatable {
        let id: Int
        let text: String
        let severity: Severity

        enum Severity {
            case error
            case warning
            case info
            case banner
            case normal
        }
    }

    private let logURL: URL
    private var timer: Timer?
    private var lastFileSize: UInt64 = 0
    private var lastModified: Date? = nil
    private let maxTailBytes: UInt64 = 256 * 1024

    init(logURL: URL) {
        self.logURL = logURL
    }

    func startTailing() {
        reloadLog(force: true)
        stopTailing()
        let t = Timer.scheduledTimer(withTimeInterval: 0.45, repeats: true) { [weak self] _ in
            Task { @MainActor [weak self] in
                self?.reloadLog(force: false)
            }
        }
        RunLoop.main.add(t, forMode: .common)
        timer = t
    }

    func stopTailing() {
        timer?.invalidate()
        timer = nil
    }

    func reloadLog(force: Bool) {
        let fm = FileManager.default
        guard fm.fileExists(atPath: logURL.path) else {
            if !lines.isEmpty || force {
                lines = []
                byteCount = 0
                lastFileSize = 0
                lastModified = nil
            }
            return
        }

        guard let attrs = try? fm.attributesOfItem(atPath: logURL.path) else { return }
        let fileSize = (attrs[.size] as? NSNumber)?.uint64Value ?? 0
        let modDate = attrs[.modificationDate] as? Date

        if !force && fileSize == lastFileSize && modDate == lastModified {
            return
        }
        lastFileSize = fileSize
        lastModified = modDate
        byteCount = fileSize

        guard let handle = try? FileHandle(forReadingFrom: logURL) else { return }
        defer { try? handle.close() }

        let startOffset: UInt64 = fileSize > maxTailBytes ? (fileSize - maxTailBytes) : 0
        if startOffset > 0 {
            try? handle.seek(toOffset: startOffset)
        }
        let data = handle.readDataToEndOfFile()
        var text = String(data: data, encoding: .utf8) ?? String(decoding: data, as: UTF8.self)
        if startOffset > 0, let newlineIdx = text.firstIndex(of: "\n") {
            text = "... (truncated earlier output) ...\n" + String(text[text.index(after: newlineIdx)...])
        }

        let rawLines = text.components(separatedBy: .newlines)
        var parsed: [LogLine] = []
        parsed.reserveCapacity(rawLines.count)
        for (idx, line) in rawLines.enumerated() {
            if idx == rawLines.count - 1 && line.isEmpty { continue }
            parsed.append(LogLine(id: idx, text: line, severity: classify(line)))
        }
        lines = parsed
    }

    func clearLog() {
        let fm = FileManager.default
        if fm.fileExists(atPath: logURL.path) {
            if let handle = try? FileHandle(forWritingTo: logURL) {
                try? handle.truncate(atOffset: 0)
                try? handle.close()
            } else {
                try? Data().write(to: logURL, options: .atomic)
            }
        }
        lastFileSize = 0
        lastModified = nil
        byteCount = 0
        lines = []
    }

    var filteredLines: [LogLine] {
        let query = filterText.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
        guard !query.isEmpty else { return lines }
        return lines.filter { $0.text.lowercased().contains(query) }
    }

    var fullVisibleText: String {
        filteredLines.map(\.text).joined(separator: "\n")
    }

    private func classify(_ line: String) -> LogLine.Severity {
        let upper = line.uppercased()
        if line.contains("[ColabManager]") {
            return .banner
        }
        if upper.contains("[ERROR]") || upper.contains("ERROR:") || upper.contains("TRACEBACK") || upper.contains("EXCEPTION") || upper.contains("CRITICAL") {
            return .error
        }
        if upper.contains("[WARNING]") || upper.contains("WARNING:") || upper.contains("WARN") {
            return .warning
        }
        if upper.contains("[INFO]") || upper.contains("UVICORN RUNNING") || upper.contains("APPLICATION STARTUP COMPLETE") {
            return .info
        }
        return .normal
    }
}

struct DaemonLogsContentView: View {
    @ObservedObject var daemonManager: BackendDaemonManager
    @ObservedObject var logModel: DaemonLogStreamModel
    let onOpenFolder: () -> Void

    @State private var copiedFeedback = false

    var body: some View {
        ZStack {
            VibrancyBackgroundRepresentable()
                .ignoresSafeArea()

            Color(red: 0.03, green: 0.05, blue: 0.10).opacity(0.68)
                .ignoresSafeArea()

            VStack(spacing: 0) {
                toolbarHeader
                Divider().overlay(Color.white.opacity(0.08))
                logViewer
            }
        }
        .preferredColorScheme(.dark)
    }

    private var toolbarHeader: some View {
        HStack(spacing: 10) {
            HStack(spacing: 8) {
                Circle()
                    .fill(statusColor)
                    .frame(width: 8, height: 8)
                    .shadow(color: statusColor.opacity(0.8), radius: 4)

                VStack(alignment: .leading, spacing: 1) {
                    HStack(spacing: 6) {
                        Text("Daemon Logs")
                            .font(.system(size: 13, weight: .semibold))
                            .foregroundStyle(.white)
                        Text(daemonManager.daemonManaged ? "Managed :1113" : "External :1113")
                            .font(.system(size: 10, weight: .medium, design: .monospaced))
                            .padding(.horizontal, 6)
                            .padding(.vertical, 1.5)
                            .background(Color.white.opacity(0.08), in: Capsule())
                            .foregroundStyle(Color.white.opacity(0.76))
                    }
                    Text("\(daemonManager.daemonLogURL.path) • \(ByteCountFormatter.string(fromByteCount: Int64(logModel.byteCount), countStyle: .file))")
                        .font(.system(size: 10, design: .monospaced))
                        .foregroundStyle(Color.white.opacity(0.45))
                        .lineLimit(1)
                        .truncationMode(.middle)
                }
            }

            Spacer()

            HStack(spacing: 6) {
                Image(systemName: "magnifyingglass")
                    .font(.system(size: 11))
                    .foregroundStyle(Color.white.opacity(0.45))
                TextField("Filter logs...", text: $logModel.filterText)
                    .textFieldStyle(.plain)
                    .font(.system(size: 11.5))
                    .frame(width: 140)
                if !logModel.filterText.isEmpty {
                    Button {
                        logModel.filterText = ""
                    } label: {
                        Image(systemName: "xmark.circle.fill")
                            .font(.system(size: 11))
                            .foregroundStyle(Color.white.opacity(0.45))
                    }
                    .buttonStyle(.plain)
                }
            }
            .padding(.horizontal, 8)
            .padding(.vertical, 5)
            .background(Color.white.opacity(0.06), in: RoundedRectangle(cornerRadius: 8))
            .overlay(
                RoundedRectangle(cornerRadius: 8)
                    .stroke(Color.white.opacity(0.10), lineWidth: 1)
            )

            Toggle(isOn: $logModel.autoScroll) {
                Image(systemName: "arrow.down.to.line")
                    .font(.system(size: 11, weight: .semibold))
            }
            .toggleStyle(.button)
            .controlSize(.small)
            .help("Auto-scroll to bottom")

            Button {
                NSPasteboard.general.clearContents()
                NSPasteboard.general.setString(logModel.fullVisibleText, forType: .string)
                copiedFeedback = true
                DispatchQueue.main.asyncAfter(deadline: .now() + 1.5) {
                    copiedFeedback = false
                }
            } label: {
                Label(copiedFeedback ? "Copied ✓" : "Copy", systemImage: copiedFeedback ? "checkmark" : "doc.on.doc")
                    .font(.system(size: 11.5, weight: .medium))
            }
            .buttonStyle(.bordered)
            .controlSize(.small)

            Button {
                logModel.clearLog()
            } label: {
                Label("Clear", systemImage: "trash")
                    .font(.system(size: 11.5, weight: .medium))
            }
            .buttonStyle(.bordered)
            .controlSize(.small)

            Button(action: onOpenFolder) {
                Label("Open Folder", systemImage: "folder")
                    .font(.system(size: 11.5, weight: .medium))
            }
            .buttonStyle(.bordered)
            .controlSize(.small)

            Button {
                Task { @MainActor in
                    await daemonManager.restartDaemon()
                }
            } label: {
                if daemonManager.isRestarting || daemonManager.isStarting {
                    HStack(spacing: 5) {
                        ProgressView().controlSize(.mini)
                        Text("Restarting...")
                            .font(.system(size: 11.5, weight: .semibold))
                    }
                } else {
                    Label("Restart Daemon", systemImage: "arrow.clockwise")
                        .font(.system(size: 11.5, weight: .semibold))
                }
            }
            .buttonStyle(.borderedProminent)
            .tint(Color(red: 0.24, green: 0.49, blue: 0.96))
            .controlSize(.small)
            .disabled(daemonManager.isRestarting || daemonManager.isStarting)
        }
        .padding(.leading, 76)
        .padding(.trailing, 14)
        .padding(.top, 12)
        .padding(.bottom, 10)
        .background(.ultraThinMaterial)
    }

    private var logViewer: some View {
        ScrollViewReader { proxy in
            ScrollView([.vertical, .horizontal], showsIndicators: true) {
                if logModel.filteredLines.isEmpty {
                    VStack(spacing: 8) {
                        Image(systemName: "doc.text")
                            .font(.system(size: 24))
                            .foregroundStyle(Color.white.opacity(0.28))
                        Text(logModel.lines.isEmpty ? "No daemon log output yet." : "No log lines match your filter.")
                            .font(.system(size: 12))
                            .foregroundStyle(Color.white.opacity(0.45))
                    }
                    .frame(maxWidth: .infinity, minHeight: 360)
                    .padding(24)
                } else {
                    LazyVStack(alignment: .leading, spacing: 2.5) {
                        ForEach(logModel.filteredLines) { line in
                            Text(line.text.isEmpty ? " " : line.text)
                                .font(.system(size: 11.5, design: .monospaced))
                                .foregroundStyle(color(for: line.severity))
                                .textSelection(.enabled)
                                .frame(maxWidth: .infinity, alignment: .leading)
                                .id(line.id)
                        }
                        Color.clear
                            .frame(height: 1)
                            .id("BOTTOM_ANCHOR")
                    }
                    .padding(14)
                }
            }
            .background(Color.black.opacity(0.28))
            .onChange(of: logModel.lines.count) { _, _ in
                if logModel.autoScroll {
                    withAnimation(.easeOut(duration: 0.15)) {
                        proxy.scrollTo("BOTTOM_ANCHOR", anchor: .bottom)
                    }
                }
            }
            .onAppear {
                if logModel.autoScroll {
                    proxy.scrollTo("BOTTOM_ANCHOR", anchor: .bottom)
                }
            }
        }
    }

    private var statusColor: Color {
        if daemonManager.daemonHealthy { return Color(red: 0.20, green: 0.85, blue: 0.60) }
        if daemonManager.isStarting || daemonManager.isRestarting { return .orange }
        return Color(red: 0.96, green: 0.34, blue: 0.36)
    }

    private func color(for severity: DaemonLogStreamModel.LogLine.Severity) -> Color {
        switch severity {
        case .error:
            return Color(red: 1.0, green: 0.50, blue: 0.52)
        case .warning:
            return Color(red: 1.0, green: 0.78, blue: 0.38)
        case .info:
            return Color(red: 0.52, green: 0.84, blue: 1.0)
        case .banner:
            return Color(red: 0.68, green: 0.66, blue: 1.0)
        case .normal:
            return Color.white.opacity(0.84)
        }
    }
}

private struct VibrancyBackgroundRepresentable: NSViewRepresentable {
    func makeNSView(context: Context) -> NSVisualEffectView {
        VibrancyBackgroundView(frame: .zero)
    }

    func updateNSView(_ nsView: NSVisualEffectView, context: Context) {}
}

@MainActor
final class DaemonLogsWindowController: NSWindowController, NSWindowDelegate {
    private let daemonManager: BackendDaemonManager
    private let logModel: DaemonLogStreamModel

    init(daemonManager: BackendDaemonManager) {
        self.daemonManager = daemonManager
        self.logModel = DaemonLogStreamModel(logURL: daemonManager.daemonLogURL)

        let rect = NSRect(x: 0, y: 0, width: 880, height: 560)
        let window = NSWindow(
            contentRect: rect,
            styleMask: [.titled, .closable, .miniaturizable, .resizable, .fullSizeContentView],
            backing: .buffered,
            defer: false
        )
        window.title = "Daemon Logs"
        window.minSize = NSSize(width: 640, height: 400)
        window.titlebarAppearsTransparent = true
        window.titleVisibility = .hidden
        window.appearance = NSAppearance(named: .darkAqua)
        window.backgroundColor = .clear
        window.isOpaque = false
        window.isReleasedWhenClosed = false
        if !window.setFrameUsingName("ColabManagerDaemonLogsWindow") {
            window.center()
        }
        window.setFrameAutosaveName("ColabManagerDaemonLogsWindow")

        super.init(window: window)
        window.delegate = self

        let contentView = DaemonLogsContentView(
            daemonManager: daemonManager,
            logModel: logModel,
            onOpenFolder: { [weak daemonManager] in
                guard let daemonManager else { return }
                daemonManager.ensureDirectoriesExist()
                if FileManager.default.fileExists(atPath: daemonManager.daemonLogURL.path) {
                    NSWorkspace.shared.activateFileViewerSelecting([daemonManager.daemonLogURL])
                } else {
                    NSWorkspace.shared.open(daemonManager.logsDir)
                }
            }
        )
        window.contentViewController = NSHostingController(rootView: contentView)
    }

    @available(*, unavailable)
    required init?(coder: NSCoder) {
        fatalError("init(coder:) has not been implemented")
    }

    func showAndFocus() {
        logModel.startTailing()
        window?.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
    }

    func windowWillClose(_ notification: Notification) {
        logModel.stopTailing()
    }
}
