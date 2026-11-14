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

        scheduleSelfTestIfRequested()
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

    // MARK: - Opt-in Automated Desktop Click Self-Test

    private func scheduleSelfTestIfRequested() {
        guard let outputPath = ProcessInfo.processInfo.environment["COLAB_DESKTOP_SELFTEST_OUT"]?
            .trimmingCharacters(in: .whitespacesAndNewlines),
              !outputPath.isEmpty else {
            return
        }

        Task { @MainActor [weak self] in
            guard let self else { return }
            await self.runDesktopClickSelfTest(outputPath: outputPath)
        }
    }

    private func runDesktopClickSelfTest(outputPath: String) async {
        let containerView = mainWindowController.workbenchContainerView
        let webView = containerView.webView

        var ready = false
        for _ in 0..<150 {
            if containerView.isSplashOverlayDismissed && !containerView.isSplashInViewHierarchy {
                ready = true
                break
            }
            try? await Task.sleep(nanoseconds: 100_000_000)
        }

        // Allow React dashboard mount & fade completion
        try? await Task.sleep(nanoseconds: 650_000_000)

        var hitTestsAllWebView = false
        var hitDetails: [[String: String]] = []
        if mainWindowController.window != nil {
            let bounds = containerView.bounds
            let probePoints: [(String, NSPoint)] = [
                ("sidebar", NSPoint(x: 120, y: 420)),
                ("mainCanvas", NSPoint(x: 660, y: 420)),
                ("topHeaderBand", NSPoint(x: 900, y: max(10, bounds.height - 24)))
            ]
            hitTestsAllWebView = true
            for (label, localPt) in probePoints {
                let superviewPt = containerView.convert(localPt, to: containerView.superview)
                let hitView = containerView.hitTest(superviewPt)
                let isWebTarget = hitView.map { $0 === webView || $0.isDescendant(of: webView) } ?? false
                if !isWebTarget {
                    hitTestsAllWebView = false
                }
                hitDetails.append([
                    "point": label,
                    "hitViewClass": hitView.map { String(describing: type(of: $0)) } ?? "nil",
                    "isWebTarget": String(isWebTarget)
                ])
            }
        }

        let targetLookupJS = """
        (function() {
          var navButtons = Array.from(document.querySelectorAll('aside nav button'));
          var target = navButtons[3] || document.querySelector('[data-testid="overview-refresh-quota"]') || document.querySelector('button');
          if (!target) {
            return JSON.stringify({ found: false, beforeText: (document.body && document.body.innerText || '').slice(0, 120) });
          }
          var rect = target.getBoundingClientRect();
          var heading = document.querySelector('main h2, h1, h2');
          return JSON.stringify({
            found: true,
            x: rect.left + rect.width / 2,
            y: rect.top + rect.height / 2,
            label: (target.innerText || '').trim(),
            beforeText: heading ? (heading.innerText || '').trim() : (document.body.innerText || '').slice(0, 120)
          });
        })();
        """

        let beforeJSON = await evaluateJSString(on: webView, script: targetLookupJS)
        var beforeText = ""
        var afterText = ""
        var targetLabel = ""
        var clickTriggeredDOMChange = false

        if let data = beforeJSON?.data(using: .utf8),
           let info = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
           (info["found"] as? Bool) == true,
           let domX = info["x"] as? Double,
           let domY = info["y"] as? Double {
            beforeText = (info["beforeText"] as? String) ?? ""
            targetLabel = (info["label"] as? String) ?? ""

            sendRealMouseClick(on: webView, containerView: containerView, domX: domX, domY: domY)
            try? await Task.sleep(nanoseconds: 450_000_000)

            let afterLookupJS = """
            (function() {
              var heading = document.querySelector('main h2, h1, h2');
              return heading ? (heading.innerText || '').trim() : (document.body.innerText || '').slice(0, 120);
            })();
            """
            afterText = (await evaluateJSString(on: webView, script: afterLookupJS)) ?? ""
            clickTriggeredDOMChange = !afterText.isEmpty && afterText != beforeText
        }

        // Verify modal geometry across multiple navigation tabs & primary action buttons
        let modalFlows: [(key: String, navIndex: Int?, buttonTexts: [String])] = [
            ("jobsSubmit", 2, ["提交任务", "Submit Job"]),
            ("templatesCreate", 4, ["新建模板", "New Template"]),
            ("notificationsAdd", 5, ["添加渠道", "Add Channel"]),
            ("sidebarNewSession", nil, ["启动实例", "创建实例", "Launch Runtime"])
        ]

        var modalChecks: [[String: Any]] = []
        for flow in modalFlows {
            if let navIndex = flow.navIndex {
                let navScript = """
                (function() {
                  var btns = Array.from(document.querySelectorAll('aside nav button'));
                  var el = btns[\(navIndex)];
                  if (!el) return JSON.stringify({ found: false });
                  var r = el.getBoundingClientRect();
                  return JSON.stringify({ found: true, x: r.left + r.width / 2, y: r.top + r.height / 2 });
                })();
                """
                if let navPos = await evaluateJSONObject(on: webView, script: navScript),
                   (navPos["found"] as? Bool) == true,
                   let nx = navPos["x"] as? Double,
                   let ny = navPos["y"] as? Double {
                    sendRealMouseClick(on: webView, containerView: containerView, domX: nx, domY: ny)
                    try? await Task.sleep(nanoseconds: 350_000_000)
                }
            }

            let literals = flow.buttonTexts
                .map { "\"\($0.replacingOccurrences(of: "\"", with: "\\\""))\"" }
                .joined(separator: ", ")
            let btnScript = """
            (function() {
              var needles = [\(literals)];
              var btns = Array.from(document.querySelectorAll('button'));
              var target = btns.find(function(b) {
                var txt = (b.innerText || '').trim();
                return needles.some(function(n) { return txt.indexOf(n) !== -1; });
              });
              if (!target) return JSON.stringify({ found: false });
              var r = target.getBoundingClientRect();
              return JSON.stringify({ found: true, x: r.left + r.width / 2, y: r.top + r.height / 2, label: (target.innerText || '').trim() });
            })();
            """
            var entry: [String: Any] = ["flow": flow.key]
            if let btnPos = await evaluateJSONObject(on: webView, script: btnScript),
               (btnPos["found"] as? Bool) == true,
               let bx = btnPos["x"] as? Double,
               let by = btnPos["y"] as? Double {
                entry["buttonLabel"] = (btnPos["label"] as? String) ?? ""
                sendRealMouseClick(on: webView, containerView: containerView, domX: bx, domY: by)
                try? await Task.sleep(nanoseconds: 350_000_000)

                let measureScript = """
                (function() {
                  var bd = document.querySelector('.mac-modal-backdrop');
                  var panel = document.querySelector('.mac-modal-panel');
                  if (!bd || !panel) {
                    return JSON.stringify({ opened: false });
                  }
                  var br = bd.getBoundingClientRect();
                  var pr = panel.getBoundingClientRect();
                  var iw = window.innerWidth;
                  var ih = window.innerHeight;
                  return JSON.stringify({
                    opened: true,
                    mountedOnBody: bd.parentElement === document.body,
                    innerWidth: iw,
                    innerHeight: ih,
                    backdrop: { left: br.left, top: br.top, width: br.width, height: br.height },
                    panel: { left: pr.left, top: pr.top, width: pr.width, height: pr.height, bottom: pr.bottom },
                    backdropCoversViewport: Math.abs(br.left) <= 1 && Math.abs(br.top) <= 1 && Math.abs(br.width - iw) <= 1 && Math.abs(br.height - ih) <= 1,
                    panelUnclippedAndCentered: pr.top >= 52 && pr.bottom <= ih - 36
                  });
                })();
                """
                if let measured = await evaluateJSONObject(on: webView, script: measureScript) {
                    for (k, v) in measured {
                        entry[k] = v
                    }
                }
                // Click empty top-left backdrop area to close modal cleanly before next flow
                sendRealMouseClick(on: webView, containerView: containerView, domX: 24, domY: 88)
                try? await Task.sleep(nanoseconds: 280_000_000)
            } else {
                entry["opened"] = false
                entry["error"] = "Trigger button not found"
            }
            modalChecks.append(entry)
        }

        let windowIsKey = mainWindowController.window?.isKeyWindow ?? false
        let firstResponderIsWebView: Bool
        if let responderView = mainWindowController.window?.firstResponder as? NSView {
            firstResponderIsWebView = (responderView === webView || responderView.isDescendant(of: webView))
        } else {
            firstResponderIsWebView = false
        }

        let report: [String: Any] = [
            "splashReady": ready,
            "isSplashOverlayDismissed": containerView.isSplashOverlayDismissed,
            "isSplashInViewHierarchy": containerView.isSplashInViewHierarchy,
            "windowIsKey": windowIsKey,
            "firstResponderIsWebView": firstResponderIsWebView,
            "hitTestsAllWebView": hitTestsAllWebView,
            "hitDetails": hitDetails,
            "clickedTargetLabel": targetLabel,
            "beforeText": beforeText,
            "afterText": afterText,
            "clickTriggeredDOMChange": clickTriggeredDOMChange,
            "modalChecks": modalChecks
        ]

        if let jsonData = try? JSONSerialization.data(withJSONObject: report, options: [.prettyPrinted, .sortedKeys]) {
            try? jsonData.write(to: URL(fileURLWithPath: outputPath))
        }

        NSApp.terminate(nil)
    }

    private func sendRealMouseClick(
        on webView: WorkbenchWebView,
        containerView: WorkbenchContainerView,
        domX: Double,
        domY: Double
    ) {
        guard let window = mainWindowController.window else { return }
        let localPoint = NSPoint(x: domX, y: max(1, webView.bounds.height - domY))
        let windowPoint = containerView.convert(localPoint, to: nil)
        let timestamp = ProcessInfo.processInfo.systemUptime

        if let downEvent = NSEvent.mouseEvent(
            with: .leftMouseDown,
            location: windowPoint,
            modifierFlags: [],
            timestamp: timestamp,
            windowNumber: window.windowNumber,
            context: nil,
            eventNumber: 1,
            clickCount: 1,
            pressure: 1.0
        ),
        let upEvent = NSEvent.mouseEvent(
            with: .leftMouseUp,
            location: windowPoint,
            modifierFlags: [],
            timestamp: timestamp + 0.03,
            windowNumber: window.windowNumber,
            context: nil,
            eventNumber: 1,
            clickCount: 1,
            pressure: 0.0
        ) {
            NSApp.sendEvent(downEvent)
            NSApp.sendEvent(upEvent)
        }
    }

    private func evaluateJSONObject(on webView: WorkbenchWebView, script: String) async -> [String: Any]? {
        guard let jsonStr = await evaluateJSString(on: webView, script: script),
              let data = jsonStr.data(using: .utf8),
              let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else {
            return nil
        }
        return obj
    }

    private func evaluateJSString(on webView: WorkbenchWebView, script: String) async -> String? {
        await withCheckedContinuation { continuation in
            webView.evaluateJavaScript(script) { result, _ in
                if let str = result as? String {
                    continuation.resume(returning: str)
                } else if let result {
                    continuation.resume(returning: String(describing: result))
                } else {
                    continuation.resume(returning: nil)
                }
            }
        }
    }
}
