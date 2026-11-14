import AppKit
import Combine
import SwiftUI
import WebKit

final class TitlebarDragStripView: NSView {
    override var mouseDownCanMoveWindow: Bool { true }

    override func mouseDown(with event: NSEvent) {
        if event.clickCount == 2 {
            window?.performZoom(nil)
        } else {
            window?.performDrag(with: event)
        }
    }
}

final class VibrancyBackgroundView: NSVisualEffectView {
    override init(frame frameRect: NSRect) {
        super.init(frame: frameRect)
        material = .underWindowBackground
        blendingMode = .behindWindow
        state = .active
        appearance = NSAppearance(named: .darkAqua)
    }

    @available(*, unavailable)
    required init?(coder: NSCoder) {
        fatalError("init(coder:) has not been implemented")
    }
}

struct BootSplashView: View {
    @ObservedObject var daemonManager: BackendDaemonManager
    let isWebLoading: Bool
    let webLoadError: String?
    let onRetry: () -> Void
    let onShowLogs: () -> Void
    let onRevealLogs: () -> Void

    @State private var pulse = false
    @State private var rotateOrbit = false

    private var activeStep: Int {
        if daemonManager.daemonHealthy {
            return isWebLoading ? 3 : 4
        }
        return daemonManager.bootPhase.rawValue
    }

    private var displayError: String? {
        daemonManager.lastError ?? webLoadError
    }

    var body: some View {
        ZStack {
            // Subtle liquid glass vignette over NSVisualEffectView
            RadialGradient(
                colors: [
                    Color(red: 0.22, green: 0.44, blue: 0.96).opacity(0.16),
                    Color(red: 0.04, green: 0.06, blue: 0.12).opacity(0.68),
                    Color(red: 0.02, green: 0.03, blue: 0.07).opacity(0.86)
                ],
                center: .center,
                startRadius: 30,
                endRadius: 560
            )
            .ignoresSafeArea()

            VStack(spacing: 26) {
                // Animated Apple-Silicon GPU Emblem
                ZStack {
                    Circle()
                        .stroke(
                            AngularGradient(
                                gradient: Gradient(colors: [
                                    Color.blue.opacity(0.65),
                                    Color.cyan.opacity(0.45),
                                    Color.orange.opacity(0.55),
                                    Color.indigo.opacity(0.65),
                                    Color.blue.opacity(0.65)
                                ]),
                                center: .center
                            ),
                            lineWidth: 1.5
                        )
                        .frame(width: 112, height: 112)
                        .rotationEffect(.degrees(rotateOrbit ? 360 : 0))
                        .opacity(displayError == nil ? 0.85 : 0.35)

                    Circle()
                        .fill(
                            RadialGradient(
                                colors: [
                                    Color(red: 0.24, green: 0.51, blue: 0.96).opacity(0.32),
                                    Color(red: 0.96, green: 0.62, blue: 0.11).opacity(0.10),
                                    Color.clear
                                ],
                                center: .center,
                                startRadius: 8,
                                endRadius: 68
                            )
                        )
                        .frame(width: 128, height: 128)
                        .scaleEffect(pulse ? 1.08 : 0.94)

                    RoundedRectangle(cornerRadius: 24, style: .continuous)
                        .fill(.ultraThinMaterial)
                        .frame(width: 82, height: 82)
                        .overlay(
                            RoundedRectangle(cornerRadius: 24, style: .continuous)
                                .stroke(
                                    LinearGradient(
                                        colors: [
                                            Color.white.opacity(0.32),
                                            Color.white.opacity(0.06)
                                        ],
                                        startPoint: .topLeading,
                                        endPoint: .bottomTrailing
                                    ),
                                    lineWidth: 1
                                )
                        )
                        .shadow(color: Color.blue.opacity(0.28), radius: 24, y: 10)

                    Image(systemName: displayError == nil ? "cpu.fill" : "exclamationmark.triangle.fill")
                        .font(.system(size: 34, weight: .semibold))
                        .foregroundStyle(
                            displayError == nil
                                ? AnyShapeStyle(
                                    LinearGradient(
                                        colors: [
                                            Color(red: 0.45, green: 0.78, blue: 1.0),
                                            Color(red: 0.36, green: 0.51, blue: 0.98),
                                            Color(red: 0.98, green: 0.68, blue: 0.22)
                                        ],
                                        startPoint: .topLeading,
                                        endPoint: .bottomTrailing
                                    )
                                )
                                : AnyShapeStyle(Color.orange)
                        )
                }

                VStack(spacing: 8) {
                    Text("Colab Manager")
                        .font(.system(size: 26, weight: .semibold, design: .rounded))
                        .foregroundStyle(.white)

                    HStack(spacing: 8) {
                        Label("Apple Silicon", systemImage: "bolt.horizontal.circle.fill")
                            .font(.system(size: 11, weight: .medium))
                            .foregroundStyle(Color(red: 0.65, green: 0.82, blue: 1.0))

                        Text("arm64")
                            .font(.system(size: 10.5, weight: .semibold, design: .monospaced))
                            .padding(.horizontal, 6)
                            .padding(.vertical, 2)
                            .background(Color.white.opacity(0.09), in: Capsule())
                            .foregroundStyle(Color.white.opacity(0.82))

                        Text("v1.2.0")
                            .font(.system(size: 10.5, weight: .medium, design: .monospaced))
                            .foregroundStyle(Color.white.opacity(0.55))
                    }
                }

                if let errorText = displayError {
                    VStack(spacing: 14) {
                        HStack(alignment: .top, spacing: 10) {
                            Image(systemName: "xmark.octagon.fill")
                                .foregroundStyle(Color.red.opacity(0.9))
                                .font(.system(size: 15))
                                .padding(.top, 1)
                            Text(errorText)
                                .font(.system(size: 12.5))
                                .foregroundStyle(Color.white.opacity(0.88))
                                .fixedSize(horizontal: false, vertical: true)
                                .textSelection(.enabled)
                        }
                        .padding(14)
                        .frame(maxWidth: 460, alignment: .leading)
                        .background(
                            RoundedRectangle(cornerRadius: 14, style: .continuous)
                                .fill(Color.red.opacity(0.12))
                                .overlay(
                                    RoundedRectangle(cornerRadius: 14, style: .continuous)
                                        .stroke(Color.red.opacity(0.32), lineWidth: 1)
                                )
                        )

                        HStack(spacing: 10) {
                            Button(action: onRetry) {
                                Label("Retry Launch", systemImage: "arrow.clockwise")
                                    .font(.system(size: 12.5, weight: .semibold))
                                    .padding(.horizontal, 14)
                                    .padding(.vertical, 8)
                            }
                            .buttonStyle(.borderedProminent)
                            .tint(Color(red: 0.24, green: 0.49, blue: 0.96))

                            Button(action: onShowLogs) {
                                Label("View Daemon Logs (⌘L)", systemImage: "doc.text.magnifyingglass")
                                    .font(.system(size: 12.5, weight: .medium))
                                    .padding(.horizontal, 12)
                                    .padding(.vertical, 8)
                            }
                            .buttonStyle(.bordered)

                            Button(action: onRevealLogs) {
                                Label("Reveal Logs", systemImage: "folder")
                                    .font(.system(size: 12.5, weight: .medium))
                                    .padding(.horizontal, 10)
                                    .padding(.vertical, 8)
                            }
                            .buttonStyle(.bordered)
                        }
                    }
                } else {
                    VStack(spacing: 14) {
                        HStack(spacing: 8) {
                            ProgressView()
                                .controlSize(.small)
                                .tint(.white)
                            Text(daemonManager.daemonHealthy && isWebLoading ? "Loading desktop workbench..." : daemonManager.startupStatus)
                                .font(.system(size: 13, weight: .medium))
                                .foregroundStyle(Color.white.opacity(0.85))
                        }

                        // Step indicator capsule
                        VStack(alignment: .leading, spacing: 7) {
                            stepRow(index: 0, label: "Probe local control plane (:1113)")
                            stepRow(index: 1, label: "Verify Python runtime & cryptography")
                            stepRow(index: 2, label: "Launch FastAPI & SSH Multiplexer")
                            stepRow(index: 3, label: "Initialize vault & load workbench UI")
                        }
                        .padding(14)
                        .frame(width: 340, alignment: .leading)
                        .background(
                            RoundedRectangle(cornerRadius: 14, style: .continuous)
                                .fill(.ultraThinMaterial)
                                .overlay(
                                    RoundedRectangle(cornerRadius: 14, style: .continuous)
                                        .stroke(Color.white.opacity(0.10), lineWidth: 1)
                                )
                        )
                    }
                }
            }
            .padding(32)
        }
        .preferredColorScheme(.dark)
        .onAppear {
            withAnimation(.easeInOut(duration: 2.2).repeatForever(autoreverses: true)) {
                pulse = true
            }
            withAnimation(.linear(duration: 8.0).repeatForever(autoreverses: false)) {
                rotateOrbit = true
            }
        }
    }

    @ViewBuilder
    private func stepRow(index: Int, label: String) -> some View {
        let isDone = activeStep > index
        let isCurrent = activeStep == index
        HStack(spacing: 9) {
            if isDone {
                Image(systemName: "checkmark.circle.fill")
                    .font(.system(size: 12))
                    .foregroundStyle(Color.green)
            } else if isCurrent {
                Image(systemName: "circle.dotted")
                    .font(.system(size: 12))
                    .foregroundStyle(Color.cyan)
            } else {
                Image(systemName: "circle")
                    .font(.system(size: 12))
                    .foregroundStyle(Color.white.opacity(0.25))
            }

            Text(label)
                .font(.system(size: 12, weight: isCurrent ? .semibold : .regular))
                .foregroundStyle(isDone || isCurrent ? Color.white.opacity(0.92) : Color.white.opacity(0.45))
        }
    }
}

@MainActor
final class WorkbenchContainerView: NSView, WKNavigationDelegate, WKUIDelegate {
    let webView: WKWebView
    private let vibrancyView: VibrancyBackgroundView
    private let dragStripView: TitlebarDragStripView
    private let splashHostingView: NSHostingView<BootSplashView>

    private let daemonManager: BackendDaemonManager
    private let bridge: NativeWebBridge
    private let onShowDaemonLogs: () -> Void
    private var cancellables: Set<AnyCancellable> = []

    private var hasLoadedInitialURL = false
    private var isWebLoading = false
    private var webLoadError: String? = nil
    private var isSplashDismissed = false

    init(
        frame: NSRect,
        daemonManager: BackendDaemonManager,
        bridge: NativeWebBridge,
        onShowDaemonLogs: @escaping () -> Void
    ) {
        self.daemonManager = daemonManager
        self.bridge = bridge
        self.onShowDaemonLogs = onShowDaemonLogs

        self.vibrancyView = VibrancyBackgroundView(frame: frame)
        self.dragStripView = TitlebarDragStripView(frame: .zero)

        let config = WKWebViewConfiguration()
        config.websiteDataStore = .default()
        config.preferences.setValue(true, forKey: "developerExtrasEnabled")
        let weakHandler = WeakScriptMessageHandler(target: bridge)
        config.userContentController.add(weakHandler, name: NativeWebBridge.handlerName)

        self.webView = WKWebView(frame: frame, configuration: config)
        self.webView.setValue(false, forKey: "drawsBackground")
        self.webView.allowsBackForwardNavigationGestures = false
        self.webView.allowsMagnification = false
        if #available(macOS 13.3, *) {
            self.webView.isInspectable = true
        }

        let placeholderSplash = BootSplashView(
            daemonManager: daemonManager,
            isWebLoading: false,
            webLoadError: nil,
            onRetry: {},
            onShowLogs: {},
            onRevealLogs: {}
        )
        self.splashHostingView = NSHostingView(rootView: placeholderSplash)

        super.init(frame: frame)

        self.webView.navigationDelegate = self
        self.webView.uiDelegate = self
        setupHierarchy()
        updateSplashView()
        observeDaemonState()
    }

    @available(*, unavailable)
    required init?(coder: NSCoder) {
        fatalError("init(coder:) has not been implemented")
    }

    override func viewDidMoveToWindow() {
        super.viewDidMoveToWindow()
        bridge.attach(webView: webView, window: window)
    }

    func reloadWorkbench() {
        injectBootstrapUserScript()
        if webView.url != nil {
            webView.reload()
        } else if daemonManager.daemonHealthy {
            loadWorkbenchURL()
        }
    }

    func toggleWebInspector() {
        webView.evaluateJavaScript("console.log('[ColabManager] Native desktop arm64 v1.2.0')", completionHandler: nil)
    }

    // MARK: - Private Layout & Lifecycle

    private func setupHierarchy() {
        wantsLayer = true
        layer?.backgroundColor = NSColor.clear.cgColor

        for subview in [vibrancyView, webView, splashHostingView, dragStripView] {
            subview.translatesAutoresizingMaskIntoConstraints = false
            addSubview(subview)
        }

        NSLayoutConstraint.activate([
            vibrancyView.leadingAnchor.constraint(equalTo: leadingAnchor),
            vibrancyView.trailingAnchor.constraint(equalTo: trailingAnchor),
            vibrancyView.topAnchor.constraint(equalTo: topAnchor),
            vibrancyView.bottomAnchor.constraint(equalTo: bottomAnchor),

            webView.leadingAnchor.constraint(equalTo: leadingAnchor),
            webView.trailingAnchor.constraint(equalTo: trailingAnchor),
            webView.topAnchor.constraint(equalTo: topAnchor),
            webView.bottomAnchor.constraint(equalTo: bottomAnchor),

            splashHostingView.leadingAnchor.constraint(equalTo: leadingAnchor),
            splashHostingView.trailingAnchor.constraint(equalTo: trailingAnchor),
            splashHostingView.topAnchor.constraint(equalTo: topAnchor),
            splashHostingView.bottomAnchor.constraint(equalTo: bottomAnchor),

            // Thin 14pt top drag strip starting past traffic light buttons so web header controls stay clickable
            dragStripView.leadingAnchor.constraint(equalTo: leadingAnchor, constant: 76),
            dragStripView.trailingAnchor.constraint(equalTo: trailingAnchor),
            dragStripView.topAnchor.constraint(equalTo: topAnchor),
            dragStripView.heightAnchor.constraint(equalToConstant: 14)
        ])
    }

    private func observeDaemonState() {
        daemonManager.$daemonHealthy
            .combineLatest(daemonManager.$isStarting, daemonManager.$isRestarting, daemonManager.$lastError)
            .receive(on: RunLoop.main)
            .sink { [weak self] healthy, starting, restarting, lastError in
                guard let self else { return }
                if restarting {
                    self.hasLoadedInitialURL = false
                    self.webLoadError = nil
                    self.showSplashOverlay()
                    return
                }
                if lastError != nil && !healthy {
                    self.hasLoadedInitialURL = false
                    self.showSplashOverlay()
                    return
                }
                if healthy && !starting && !self.hasLoadedInitialURL {
                    self.hasLoadedInitialURL = true
                    self.loadWorkbenchURL()
                }
            }
            .store(in: &cancellables)
    }

    private func loadWorkbenchURL() {
        webLoadError = nil
        isWebLoading = true
        updateSplashView()
        injectBootstrapUserScript()

        var request = URLRequest(url: daemonManager.baseURL)
        request.cachePolicy = .reloadIgnoringLocalCacheData
        webView.load(request)
    }

    private func injectBootstrapUserScript() {
        let ucc = webView.configuration.userContentController
        ucc.removeAllUserScripts()

        let tokenValue: Any = daemonManager.autoAuthToken ?? NSNull()
        let configDict: [String: Any] = [
            "isMacApp": true,
            "arch": "arm64",
            "version": "1.2.0",
            "dataDir": daemonManager.dataDir.path,
            "workspaceDir": daemonManager.workspaceDir.path,
            "daemonManaged": daemonManager.daemonManaged,
            "autoAuthToken": tokenValue
        ]

        guard let data = try? JSONSerialization.data(withJSONObject: configDict, options: []),
              let jsonLiteral = String(data: data, encoding: .utf8) else {
            return
        }

        let source = """
        (function() {
          window.__COLAB_MACOS__ = \(jsonLiteral);
          try {
            if (window.__COLAB_MACOS__.autoAuthToken && !localStorage.getItem('token')) {
              localStorage.setItem('token', window.__COLAB_MACOS__.autoAuthToken);
            }
          } catch (_) {}
        })();
        """

        let script = WKUserScript(
            source: source,
            injectionTime: .atDocumentStart,
            forMainFrameOnly: true
        )
        ucc.addUserScript(script)
    }

    private func updateSplashView() {
        splashHostingView.rootView = BootSplashView(
            daemonManager: daemonManager,
            isWebLoading: isWebLoading,
            webLoadError: webLoadError,
            onRetry: { [weak self] in
                guard let self else { return }
                self.webLoadError = nil
                self.hasLoadedInitialURL = false
                self.showSplashOverlay()
                Task { @MainActor in
                    await self.daemonManager.restartDaemon()
                }
            },
            onShowLogs: { [weak self] in
                self?.onShowDaemonLogs()
            },
            onRevealLogs: { [weak self] in
                self?.bridge.revealInFinder(target: "logs")
            }
        )
    }

    private func showSplashOverlay() {
        isSplashDismissed = false
        splashHostingView.isHidden = false
        splashHostingView.alphaValue = 1.0
        updateSplashView()
    }

    private func dismissSplashOverlayIfReady() {
        guard daemonManager.daemonHealthy, !isWebLoading, webLoadError == nil, !isSplashDismissed else {
            return
        }
        isSplashDismissed = true
        NSAnimationContext.runAnimationGroup { context in
            context.duration = 0.35
            context.timingFunction = CAMediaTimingFunction(name: .easeOut)
            splashHostingView.animator().alphaValue = 0.0
        } completionHandler: { [weak self] in
            Task { @MainActor [weak self] in
                guard let self, self.isSplashDismissed else { return }
                self.splashHostingView.isHidden = true
            }
        }
    }

    // MARK: - WKNavigationDelegate

    func webView(
        _ webView: WKWebView,
        decidePolicyFor navigationAction: WKNavigationAction,
        decisionHandler: @escaping (WKNavigationActionPolicy) -> Void
    ) {
        guard let url = navigationAction.request.url else {
            decisionHandler(.allow)
            return
        }

        let scheme = url.scheme?.lowercased() ?? ""
        if scheme == "about" || scheme == "blob" || scheme == "data" {
            decisionHandler(.allow)
            return
        }

        let host = url.host?.lowercased() ?? ""
        let port = url.port ?? (scheme == "https" ? 443 : 80)
        let isLocalBackend = (host == "127.0.0.1" || host == "localhost") && port == 1113

        if isLocalBackend {
            if navigationAction.targetFrame == nil {
                webView.load(navigationAction.request)
                decisionHandler(.cancel)
            } else {
                decisionHandler(.allow)
            }
            return
        }

        if ["http", "https", "mailto", "cursor", "vscode"].contains(scheme) {
            NSWorkspace.shared.open(url)
        }
        decisionHandler(.cancel)
    }

    func webView(_ webView: WKWebView, didStartProvisionalNavigation navigation: WKNavigation!) {
        isWebLoading = true
        bridge.markWebViewReady(false)
        updateSplashView()
    }

    func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
        isWebLoading = false
        webLoadError = nil
        bridge.markWebViewReady(true)
        updateSplashView()
        dismissSplashOverlayIfReady()
    }

    func webView(_ webView: WKWebView, didFail navigation: WKNavigation!, withError error: Error) {
        handleWebLoadFailure(error)
    }

    func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!, withError error: Error) {
        handleWebLoadFailure(error)
    }

    private func handleWebLoadFailure(_ error: Error) {
        let nsError = error as NSError
        if nsError.domain == NSURLErrorDomain && nsError.code == NSURLErrorCancelled {
            return
        }
        isWebLoading = false
        webLoadError = "Unable to load local workbench: \(error.localizedDescription)"
        showSplashOverlay()
    }

    // MARK: - WKUIDelegate

    func webView(
        _ webView: WKWebView,
        createWebViewWith configuration: WKWebViewConfiguration,
        for navigationAction: WKNavigationAction,
        windowFeatures: WKWindowFeatures
    ) -> WKWebView? {
        if let url = navigationAction.request.url {
            let host = url.host?.lowercased() ?? ""
            let port = url.port ?? 80
            if (host == "127.0.0.1" || host == "localhost") && port == 1113 {
                webView.load(navigationAction.request)
            } else {
                NSWorkspace.shared.open(url)
            }
        }
        return nil
    }

    func webView(
        _ webView: WKWebView,
        runOpenPanelWith parameters: WKOpenPanelParameters,
        initiatedByFrame frame: WKFrameInfo,
        completionHandler: @escaping ([URL]?) -> Void
    ) {
        let panel = NSOpenPanel()
        panel.canChooseFiles = true
        panel.canChooseDirectories = parameters.allowsDirectories
        panel.allowsMultipleSelection = parameters.allowsMultipleSelection
        panel.resolvesAliases = true
        panel.prompt = "Upload"

        if let hostWindow = window {
            panel.beginSheetModal(for: hostWindow) { response in
                completionHandler(response == .OK ? panel.urls : nil)
            }
        } else {
            let response = panel.runModal()
            completionHandler(response == .OK ? panel.urls : nil)
        }
    }

    func webView(
        _ webView: WKWebView,
        runJavaScriptAlertPanelWithMessage message: String,
        initiatedByFrame frame: WKFrameInfo,
        completionHandler: @escaping () -> Void
    ) {
        let alert = NSAlert()
        alert.messageText = "Colab Manager"
        alert.informativeText = message
        alert.alertStyle = .informational
        alert.addButton(withTitle: "OK")
        if let hostWindow = window {
            alert.beginSheetModal(for: hostWindow) { _ in completionHandler() }
        } else {
            alert.runModal()
            completionHandler()
        }
    }

    func webView(
        _ webView: WKWebView,
        runJavaScriptConfirmPanelWithMessage message: String,
        initiatedByFrame frame: WKFrameInfo,
        completionHandler: @escaping (Bool) -> Void
    ) {
        let alert = NSAlert()
        alert.messageText = "Colab Manager"
        alert.informativeText = message
        alert.alertStyle = .informational
        alert.addButton(withTitle: "Confirm")
        alert.addButton(withTitle: "Cancel")
        if let hostWindow = window {
            alert.beginSheetModal(for: hostWindow) { response in
                completionHandler(response == .alertFirstButtonReturn)
            }
        } else {
            let response = alert.runModal()
            completionHandler(response == .alertFirstButtonReturn)
        }
    }

    func webView(
        _ webView: WKWebView,
        runJavaScriptTextInputPanelWithPrompt prompt: String,
        defaultText: String?,
        initiatedByFrame frame: WKFrameInfo,
        completionHandler: @escaping (String?) -> Void
    ) {
        let alert = NSAlert()
        alert.messageText = "Colab Manager"
        alert.informativeText = prompt
        alert.addButton(withTitle: "OK")
        alert.addButton(withTitle: "Cancel")

        let input = NSTextField(frame: NSRect(x: 0, y: 0, width: 280, height: 24))
        input.stringValue = defaultText ?? ""
        alert.accessoryView = input

        if let hostWindow = window {
            alert.beginSheetModal(for: hostWindow) { response in
                completionHandler(response == .alertFirstButtonReturn ? input.stringValue : nil)
            }
        } else {
            let response = alert.runModal()
            completionHandler(response == .alertFirstButtonReturn ? input.stringValue : nil)
        }
    }
}
