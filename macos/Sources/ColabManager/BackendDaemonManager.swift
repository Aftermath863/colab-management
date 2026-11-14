import Combine
import Foundation
import Security

@MainActor
final class BackendDaemonManager: ObservableObject {
    @Published private(set) var totalCcu: Double = 0.0
    @Published private(set) var burnRate: Double = 0.0
    @Published private(set) var activeSessions: [SessionSummary] = []
    @Published private(set) var accounts: [AccountSummary] = []
    @Published private(set) var webReportedActiveRuntimes: Int? = nil

    @Published private(set) var daemonHealthy: Bool = false
    @Published private(set) var daemonManaged: Bool = false
    @Published private(set) var lastError: String? = nil
    @Published private(set) var bootPhase: DaemonBootPhase = .probing
    @Published private(set) var startupStatus: String = DaemonBootPhase.probing.title
    @Published private(set) var isStarting: Bool = false
    @Published private(set) var isRestarting: Bool = false
    @Published private(set) var isSyncingAccounts: Bool = false
    @Published private(set) var autoAuthToken: String? = nil
    @Published private(set) var resolvedPythonPath: String? = nil

    let baseURL = URL(string: "http://127.0.0.1:1113")!
    let appRoot: URL
    let dataDir: URL
    let workspaceDir: URL
    let logsDir: URL
    let daemonLogURL: URL

    private let passwordFileURL: URL
    private let urlSession: URLSession
    private let probeSession: URLSession

    private var managedProcess: Process?
    private var logFileHandle: FileHandle?
    private var pollTimer: Timer?
    private var isShuttingDown = false
    private var daemonLaunchTime: Date?
    private var consecutiveCrashes = 0
    private var startTask: Task<Void, Never>?

    init() {
        let fm = FileManager.default
        let appSupport = fm.urls(for: .applicationSupportDirectory, in: .userDomainMask).first
            ?? fm.homeDirectoryForCurrentUser.appendingPathComponent("Library/Application Support", isDirectory: true)
        let desktopRoot = appSupport.appendingPathComponent("ColabManager", isDirectory: true)
        self.dataDir = desktopRoot.appendingPathComponent("data", isDirectory: true)
        self.workspaceDir = desktopRoot.appendingPathComponent("workspace", isDirectory: true)
        self.logsDir = self.dataDir.appendingPathComponent("logs", isDirectory: true)
        self.daemonLogURL = self.logsDir.appendingPathComponent("daemon.log")
        self.passwordFileURL = self.dataDir.appendingPathComponent(".desktop_admin_password")
        self.appRoot = Self.resolveAppRoot()

        let standardConfig = URLSessionConfiguration.ephemeral
        standardConfig.timeoutIntervalForRequest = 10
        standardConfig.timeoutIntervalForResource = 15
        self.urlSession = URLSession(configuration: standardConfig)

        let probeConfig = URLSessionConfiguration.ephemeral
        probeConfig.timeoutIntervalForRequest = 1.5
        probeConfig.timeoutIntervalForResource = 2.0
        self.probeSession = URLSession(configuration: probeConfig)

        ensureDirectoriesExist()
    }

    var effectiveActiveRuntimeCount: Int {
        if !activeSessions.isEmpty {
            return activeSessions.count
        }
        if let webCount = webReportedActiveRuntimes {
            return webCount
        }
        return 0
    }

    func ensureDirectoriesExist() {
        let fm = FileManager.default
        for dir in [dataDir, workspaceDir, logsDir] {
            try? fm.createDirectory(at: dir, withIntermediateDirectories: true)
        }
    }

    func start() async {
        if isShuttingDown { return }
        if let existingTask = startTask {
            await existingTask.value
            return
        }

        let task = Task { [weak self] in
            guard let self else { return }
            await self.runStartupSequence()
        }
        startTask = task
        await task.value
        startTask = nil
    }

    func restartDaemon() async {
        guard !isRestarting else { return }
        if let activeStart = startTask {
            await activeStart.value
        }
        isRestarting = true
        lastError = nil
        daemonHealthy = false
        updatePhase(.probing, status: "Restarting local control plane...")

        if daemonManaged {
            await terminateManagedProcessAndWait()
        }

        consecutiveCrashes = 0
        await runStartupSequence()
        isRestarting = false
    }

    func terminateIfManaged() {
        isShuttingDown = true
        stopPolling()

        guard daemonManaged, let proc = managedProcess else { return }
        if proc.isRunning {
            appendLogBanner("[ColabManager] Sending SIGTERM to managed backend PID \(proc.processIdentifier)")
            proc.terminate()

            let deadline = Date().addingTimeInterval(2.0)
            while proc.isRunning && Date() < deadline {
                Thread.sleep(forTimeInterval: 0.05)
            }
            if proc.isRunning {
                appendLogBanner("[ColabManager] Force-stopping backend PID \(proc.processIdentifier) with SIGKILL")
                kill(proc.processIdentifier, SIGKILL)
            }
        }
        closeLogHandle()
        managedProcess = nil
    }

    func applyWebFleetSummary(activeRuntimes: Int?, totalCcu: Double?, burnRate: Double?) {
        if let activeRuntimes {
            self.webReportedActiveRuntimes = max(0, activeRuntimes)
        }
        if let totalCcu, self.accounts.isEmpty {
            self.totalCcu = totalCcu
        }
        if let burnRate, self.accounts.isEmpty {
            self.burnRate = burnRate
        }
    }

    func refreshFleetNow() async {
        let healthy = await probeHealthz()
        daemonHealthy = healthy
        guard healthy else { return }

        if autoAuthToken == nil, daemonManaged {
            if let password = readExistingPasswordFile() {
                autoAuthToken = await attemptLogin(password: password)
            }
        }

        guard let token = autoAuthToken else { return }
        await fetchFleetState(token: token)
    }

    func syncAllAccounts() async -> Bool {
        guard daemonHealthy else { return false }
        if autoAuthToken == nil {
            await attemptAutoLoginCandidates()
        }
        guard let token = autoAuthToken else { return false }

        isSyncingAccounts = true
        defer { isSyncingAccounts = false }

        var req = URLRequest(url: baseURL.appendingPathComponent("api/accounts/sync-all"))
        req.httpMethod = "POST"
        req.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        req.timeoutInterval = 30

        do {
            let (data, response) = try await urlSession.data(for: req)
            guard let http = response as? HTTPURLResponse else { return false }
            if http.statusCode == 401 {
                autoAuthToken = nil
                return false
            }
            if (200..<300).contains(http.statusCode) {
                if let synced = try? JSONDecoder().decode([AccountSummary].self, from: data) {
                    applyAccounts(synced)
                }
                await fetchSessions(token: token)
                return true
            }
        } catch {
            appendLogBanner("[ColabManager] Sync accounts request failed: \(error.localizedDescription)")
        }
        return false
    }

    // MARK: - Startup Implementation

    private func runStartupSequence() async {
        ensureDirectoriesExist()
        isStarting = true
        lastError = nil
        updatePhase(.probing)

        if await probeHealthz() {
            daemonManaged = false
            daemonHealthy = true
            updatePhase(.authenticating, status: "Connected to running daemon on :1113")
            await attemptAutoLoginCandidates()
            updatePhase(.ready)
            isStarting = false
            await refreshFleetNow()
            startPolling()
            return
        }

        updatePhase(.resolvingPython)
        let appRootPath = appRoot.path
        let dataPath = dataDir.path
        let logPath = daemonLogURL.path

        let pythonResult = await Task.detached(priority: .userInitiated) {
            Self.findOrProvisionPython(appRootPath: appRootPath, dataDirPath: dataPath, logPath: logPath)
        }.value

        guard let pythonPath = pythonResult.pythonPath else {
            daemonHealthy = false
            isStarting = false
            lastError = pythonResult.errorMessage ?? "Unable to locate a verified Python 3 runtime with FastAPI, uvicorn, asyncssh, and cryptography."
            startupStatus = "Python runtime setup failed"
            return
        }
        resolvedPythonPath = pythonPath

        let adminPassword: String
        do {
            adminPassword = try loadOrCreateDesktopPassword()
        } catch {
            daemonHealthy = false
            isStarting = false
            lastError = "Failed to initialize desktop admin password: \(error.localizedDescription)"
            startupStatus = "Vault initialization failed"
            return
        }

        updatePhase(.launchingDaemon)
        do {
            let proc = try spawnBackendProcess(pythonPath: pythonPath, adminPassword: adminPassword)
            managedProcess = proc
            daemonManaged = true
            daemonLaunchTime = Date()
        } catch {
            daemonHealthy = false
            daemonManaged = false
            isStarting = false
            lastError = "Failed to launch backend daemon: \(error.localizedDescription)"
            startupStatus = "Backend launch failed"
            return
        }

        let ready = await waitForDaemonHealthy(timeoutSeconds: 30.0)
        if !ready {
            isStarting = false
            if lastError == nil {
                lastError = "Backend daemon on 127.0.0.1:1113 did not become healthy within 30 seconds. Check Daemon Logs (⌘L)."
            }
            startupStatus = "Backend health check timed out"
            return
        }

        daemonHealthy = true
        updatePhase(.authenticating)
        autoAuthToken = await attemptLogin(password: adminPassword)
        updatePhase(.ready)
        lastError = nil
        isStarting = false
        await refreshFleetNow()
        startPolling()
    }

    private func updatePhase(_ phase: DaemonBootPhase, status: String? = nil) {
        bootPhase = phase
        startupStatus = status ?? phase.title
    }

    private func waitForDaemonHealthy(timeoutSeconds: TimeInterval) async -> Bool {
        let deadline = Date().addingTimeInterval(timeoutSeconds)
        while Date() < deadline {
            if isShuttingDown { return false }
            if let proc = managedProcess, !proc.isRunning {
                lastError = "Backend process terminated during startup (exit code \(proc.terminationStatus)). Inspect Daemon Logs (⌘L)."
                return false
            }
            if await probeHealthz() {
                return true
            }
            try? await Task.sleep(nanoseconds: 250_000_000)
        }
        return false
    }

    private func probeHealthz() async -> Bool {
        var req = URLRequest(url: baseURL.appendingPathComponent("healthz"))
        req.httpMethod = "GET"
        req.cachePolicy = .reloadIgnoringLocalAndRemoteCacheData

        do {
            let (data, response) = try await probeSession.data(for: req)
            guard let http = response as? HTTPURLResponse, http.statusCode == 200 else {
                return false
            }
            if let decoded = try? JSONDecoder().decode(HealthzResponse.self, from: data) {
                return decoded.status.lowercased() == "ok"
            }
            return true
        } catch {
            return false
        }
    }

    private func attemptAutoLoginCandidates() async {
        var candidates: [String] = []
        if let stored = readExistingPasswordFile(), !stored.isEmpty {
            candidates.append(stored)
        }
        if let envPass = ProcessInfo.processInfo.environment["ADMIN_PASSWORD"]?.trimmingCharacters(in: .whitespacesAndNewlines),
           !envPass.isEmpty,
           !candidates.contains(envPass) {
            candidates.append(envPass)
        }
        if !candidates.contains("changeme") {
            candidates.append("changeme")
        }

        for candidate in candidates {
            if let token = await attemptLogin(password: candidate) {
                autoAuthToken = token
                return
            }
        }
    }

    private func attemptLogin(password: String) async -> String? {
        var req = URLRequest(url: baseURL.appendingPathComponent("api/auth/login"))
        req.httpMethod = "POST"
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.httpBody = try? JSONEncoder().encode(LoginRequestPayload(password: password))

        do {
            let (data, response) = try await urlSession.data(for: req)
            guard let http = response as? HTTPURLResponse, (200..<300).contains(http.statusCode) else {
                return nil
            }
            let decoded = try JSONDecoder().decode(LoginResponse.self, from: data)
            return decoded.token
        } catch {
            return nil
        }
    }

    // MARK: - Process Management

    private func spawnBackendProcess(pythonPath: String, adminPassword: String) throws -> Process {
        let logHandle = try openDaemonLogHandle(banner: "[ColabManager] Starting backend daemon using \(pythonPath) (appRoot=\(appRoot.path))")
        self.logFileHandle = logHandle

        let proc = Process()
        proc.executableURL = URL(fileURLWithPath: pythonPath)
        proc.arguments = ["-m", "uvicorn", "backend.app.main:app", "--host", "127.0.0.1", "--port", "1113"]
        proc.currentDirectoryURL = appRoot

        var env = ProcessInfo.processInfo.environment
        let defaultPathEntries = [
            "/opt/homebrew/bin",
            "/usr/local/bin",
            "/usr/bin",
            "/bin",
            "/usr/sbin",
            "/sbin"
        ]
        let existingEntries = (env["PATH"] ?? "")
            .split(separator: ":")
            .map(String.init)
            .filter { !$0.isEmpty }
        var combinedPath: [String] = []
        for entry in defaultPathEntries + existingEntries where !combinedPath.contains(entry) {
            combinedPath.append(entry)
        }

        env["PATH"] = combinedPath.joined(separator: ":")
        env["PYTHONPATH"] = appRoot.path
        env["STATIC_DIR"] = appRoot.appendingPathComponent("frontend/dist").path
        env["DATA_DIR"] = dataDir.path
        env["WORKSPACE_DIR"] = workspaceDir.path
        env["ADMIN_PASSWORD"] = adminPassword
        env["PORT"] = "1113"
        env["HOST"] = "127.0.0.1"
        env["PYTHONUNBUFFERED"] = "1"
        proc.environment = env

        proc.standardOutput = logHandle
        proc.standardError = logHandle

        proc.terminationHandler = { [weak self] exitedProcess in
            let exitStatus = exitedProcess.terminationStatus
            Task { @MainActor [weak self] in
                self?.handleDaemonExited(process: exitedProcess, status: exitStatus)
            }
        }

        try proc.run()
        return proc
    }

    private func handleDaemonExited(process: Process, status: Int32) {
        guard managedProcess === process else { return }
        closeLogHandle()
        managedProcess = nil

        if isShuttingDown || isRestarting {
            return
        }

        daemonHealthy = false
        appendLogBanner("[ColabManager] Managed backend exited unexpectedly with status \(status)")

        if let launchTime = daemonLaunchTime, Date().timeIntervalSince(launchTime) > 60 {
            consecutiveCrashes = 0
        }
        consecutiveCrashes += 1

        if consecutiveCrashes <= 5 {
            let delay = min(Double(consecutiveCrashes) * 1.5, 5.0)
            lastError = "Backend exited unexpectedly (code \(status)). Restarting automatically..."
            startupStatus = "Recovering backend daemon..."
            Task { [weak self] in
                try? await Task.sleep(nanoseconds: UInt64(delay * 1_000_000_000))
                guard let self, !self.isShuttingDown, !self.isRestarting else { return }
                await self.start()
            }
        } else {
            lastError = "Backend daemon crashed repeatedly (exit code \(status)). Inspect Daemon Logs (⌘L) and click Retry."
            startupStatus = "Daemon restart limit reached"
        }
    }

    private func terminateManagedProcessAndWait() async {
        guard let proc = managedProcess else { return }
        managedProcess = nil
        if proc.isRunning {
            appendLogBanner("[ColabManager] Stopping managed backend PID \(proc.processIdentifier) for restart")
            proc.terminate()
            await Task.detached {
                let deadline = Date().addingTimeInterval(2.0)
                while proc.isRunning && Date() < deadline {
                    try? await Task.sleep(nanoseconds: 50_000_000)
                }
                if proc.isRunning {
                    kill(proc.processIdentifier, SIGKILL)
                }
            }.value
        }
        closeLogHandle()
        daemonManaged = false
    }

    // MARK: - Polling

    private func startPolling() {
        stopPolling()
        let timer = Timer.scheduledTimer(withTimeInterval: 5.0, repeats: true) { [weak self] _ in
            Task { @MainActor [weak self] in
                await self?.refreshFleetNow()
            }
        }
        RunLoop.main.add(timer, forMode: .common)
        pollTimer = timer
    }

    private func stopPolling() {
        pollTimer?.invalidate()
        pollTimer = nil
    }

    private func fetchFleetState(token: String) async {
        await withTaskGroup(of: Void.self) { group in
            group.addTask { @MainActor [weak self] in
                await self?.fetchAccounts(token: token)
            }
            group.addTask { @MainActor [weak self] in
                await self?.fetchSessions(token: token)
            }
        }
    }

    private func fetchAccounts(token: String) async {
        var req = URLRequest(url: baseURL.appendingPathComponent("api/accounts"))
        req.httpMethod = "GET"
        req.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")

        do {
            let (data, response) = try await urlSession.data(for: req)
            guard let http = response as? HTTPURLResponse else { return }
            if http.statusCode == 401 {
                autoAuthToken = nil
                return
            }
            if http.statusCode == 200 {
                let decoded = try JSONDecoder().decode([AccountSummary].self, from: data)
                applyAccounts(decoded)
            }
        } catch {
            // Transient poll errors are tolerated
        }
    }

    private func fetchSessions(token: String) async {
        var req = URLRequest(url: baseURL.appendingPathComponent("api/sessions"))
        req.httpMethod = "GET"
        req.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")

        do {
            let (data, response) = try await urlSession.data(for: req)
            guard let http = response as? HTTPURLResponse else { return }
            if http.statusCode == 401 {
                autoAuthToken = nil
                return
            }
            if http.statusCode == 200 {
                let decoded = try JSONDecoder().decode([SessionSummary].self, from: data)
                self.activeSessions = decoded.filter { $0.isRunning }
            }
        } catch {
            // Transient poll errors are tolerated
        }
    }

    private func applyAccounts(_ accounts: [AccountSummary]) {
        self.accounts = accounts
        self.totalCcu = accounts.reduce(0.0) { $0 + $1.ccu_balance }
        self.burnRate = accounts.reduce(0.0) { $0 + $1.burn_rate_hourly }
    }

    // MARK: - Password & Logging Helpers

    private func readExistingPasswordFile() -> String? {
        guard let raw = try? String(contentsOf: passwordFileURL, encoding: .utf8) else { return nil }
        let trimmed = raw.trimmingCharacters(in: .whitespacesAndNewlines)
        return trimmed.isEmpty ? nil : trimmed
    }

    private func loadOrCreateDesktopPassword() throws -> String {
        let fm = FileManager.default
        if let existing = readExistingPasswordFile() {
            try? fm.setAttributes([.posixPermissions: 0o600], ofItemAtPath: passwordFileURL.path)
            return existing
        }

        var bytes = [UInt8](repeating: 0, count: 24)
        let status = SecRandomCopyBytes(kSecRandomDefault, bytes.count, &bytes)
        let secret: String
        if status == errSecSuccess {
            secret = bytes.map { String(format: "%02x", $0) }.joined()
        } else {
            secret = UUID().uuidString.replacingOccurrences(of: "-", with: "")
                + UUID().uuidString.replacingOccurrences(of: "-", with: "")
        }

        try secret.write(to: passwordFileURL, atomically: true, encoding: .utf8)
        try fm.setAttributes([.posixPermissions: 0o600], ofItemAtPath: passwordFileURL.path)
        return secret
    }

    private func openDaemonLogHandle(banner: String) throws -> FileHandle {
        let fm = FileManager.default
        if !fm.fileExists(atPath: daemonLogURL.path) {
            fm.createFile(atPath: daemonLogURL.path, contents: nil, attributes: [.posixPermissions: 0o644])
        }
        let handle = try FileHandle(forWritingTo: daemonLogURL)
        handle.seekToEndOfFile()
        let line = "\n=== \(ISO8601DateFormatter().string(from: Date())) \(banner) ===\n"
        if let data = line.data(using: .utf8) {
            handle.write(data)
        }
        return handle
    }

    private func appendLogBanner(_ message: String) {
        let line = "\n[\(ISO8601DateFormatter().string(from: Date()))] \(message)\n"
        guard let data = line.data(using: .utf8) else { return }
        if let handle = logFileHandle {
            handle.write(data)
        } else if FileManager.default.fileExists(atPath: daemonLogURL.path),
                  let handle = try? FileHandle(forWritingTo: daemonLogURL) {
            handle.seekToEndOfFile()
            handle.write(data)
            try? handle.close()
        }
    }

    private func closeLogHandle() {
        if let handle = logFileHandle {
            try? handle.close()
            logFileHandle = nil
        }
    }

    // MARK: - App Root & Python Discovery (Nonisolated)

    nonisolated private static func resolveAppRoot() -> URL {
        let fm = FileManager.default

        if let bundledApp = Bundle.main.resourceURL?.appendingPathComponent("app", isDirectory: true),
           fm.fileExists(atPath: bundledApp.appendingPathComponent("backend/app/main.py").path) {
            return bundledApp
        }

        var searchRoots: [URL] = [
            URL(fileURLWithPath: fm.currentDirectoryPath, isDirectory: true)
        ]
        if let execURL = Bundle.main.executableURL?.deletingLastPathComponent() {
            searchRoots.append(execURL)
        }
        if let arg0 = CommandLine.arguments.first {
            let argURL = URL(fileURLWithPath: arg0).deletingLastPathComponent()
            searchRoots.append(argURL)
        }

        var fallbackBackendOnly: URL?

        for start in searchRoots {
            var cursor = start.standardizedFileURL
            for _ in 0..<8 {
                let mainPy = cursor.appendingPathComponent("backend/app/main.py").path
                let distIndex = cursor.appendingPathComponent("frontend/dist/index.html").path
                if fm.fileExists(atPath: mainPy) {
                    if fm.fileExists(atPath: distIndex) {
                        return cursor
                    }
                    if fallbackBackendOnly == nil {
                        fallbackBackendOnly = cursor
                    }
                }
                let parent = cursor.deletingLastPathComponent()
                if parent.path == cursor.path { break }
                cursor = parent
            }
        }

        return fallbackBackendOnly ?? URL(fileURLWithPath: fm.currentDirectoryPath, isDirectory: true)
    }

    nonisolated private static func findOrProvisionPython(
        appRootPath: String,
        dataDirPath: String,
        logPath: String
    ) -> (pythonPath: String?, errorMessage: String?) {
        let fm = FileManager.default
        let appRootURL = URL(fileURLWithPath: appRootPath, isDirectory: true)
        let dataDirURL = URL(fileURLWithPath: dataDirPath, isDirectory: true)
        let venvDirURL = dataDirURL.appendingPathComponent("venv", isDirectory: true)
        let venvPythonPath = venvDirURL.appendingPathComponent("bin/python3").path

        var candidates: [String] = []
        func appendCandidate(_ path: String?) {
            guard let raw = path?.trimmingCharacters(in: .whitespacesAndNewlines), !raw.isEmpty else { return }
            let expanded = (raw as NSString).expandingTildeInPath
            if !candidates.contains(expanded) {
                candidates.append(expanded)
            }
        }

        appendCandidate(ProcessInfo.processInfo.environment["COLAB_PYTHON_BIN"])

        let hintURL = appRootURL.appendingPathComponent(".python_hint")
        if let hintContent = try? String(contentsOf: hintURL, encoding: .utf8) {
            for line in hintContent.components(separatedBy: .newlines) {
                let trimmed = line.trimmingCharacters(in: .whitespacesAndNewlines)
                if !trimmed.isEmpty && !trimmed.hasPrefix("#") {
                    appendCandidate(trimmed)
                    break
                }
            }
        }

        appendCandidate(venvPythonPath)
        appendCandidate("/opt/homebrew/Caskroom/miniconda/base/bin/python3")
        appendCandidate("/opt/homebrew/bin/python3")
        appendCandidate("/usr/local/bin/python3")
        appendCandidate("/usr/bin/python3")

        if let whichPath = runAndCapture(
            executable: "/usr/bin/env",
            arguments: ["PATH=/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin", "which", "python3"]
        )?.trimmingCharacters(in: .whitespacesAndNewlines) {
            appendCandidate(whichPath)
        }

        for candidate in candidates {
            guard fm.isExecutableFile(atPath: candidate) else { continue }
            if verifyPythonImports(pythonPath: candidate) {
                return (candidate, nil)
            }
        }

        // Provision a local virtual environment under Application Support/ColabManager/data/venv
        var basePython: String?
        for candidate in candidates where candidate != venvPythonPath {
            if fm.isExecutableFile(atPath: candidate), verifyPythonVersionAtLeast310(pythonPath: candidate) {
                basePython = candidate
                break
            }
        }
        if basePython == nil {
            basePython = candidates.first(where: { $0 != venvPythonPath && fm.isExecutableFile(atPath: $0) })
        }

        guard let base = basePython else {
            return (
                nil,
                "No Python 3 interpreter found. Install Python 3.10+ (e.g. via Homebrew) or set COLAB_PYTHON_BIN."
            )
        }

        appendDiagnosticLog(logPath: logPath, message: "[ColabManager] Creating isolated desktop Python venv at \(venvDirURL.path) from \(base)")

        if !fm.fileExists(atPath: venvPythonPath) {
            let venvStatus = runLoggedCommand(executable: base, arguments: ["-m", "venv", venvDirURL.path], logPath: logPath)
            guard venvStatus == 0, fm.isExecutableFile(atPath: venvPythonPath) else {
                return (
                    nil,
                    "Failed to create Python virtual environment at \(venvDirURL.path) using \(base). Check Daemon Logs (⌘L)."
                )
            }
        }

        let requirementsURL = appRootURL.appendingPathComponent("backend/requirements.txt")
        if fm.fileExists(atPath: requirementsURL.path) {
            appendDiagnosticLog(logPath: logPath, message: "[ColabManager] Installing backend dependencies from \(requirementsURL.path)")
            _ = runLoggedCommand(executable: venvPythonPath, arguments: ["-m", "pip", "install", "--upgrade", "pip"], logPath: logPath)
            let pipStatus = runLoggedCommand(
                executable: venvPythonPath,
                arguments: ["-m", "pip", "install", "-r", requirementsURL.path],
                logPath: logPath
            )
            if pipStatus != 0 {
                return (
                    nil,
                    "Failed to install Python dependencies from requirements.txt (exit code \(pipStatus)). Inspect Daemon Logs (⌘L)."
                )
            }
        }

        if verifyPythonImports(pythonPath: venvPythonPath) {
            return (venvPythonPath, nil)
        }

        return (
            nil,
            "Python environment at \(venvPythonPath) is missing required packages (fastapi, uvicorn, asyncssh, cryptography). Inspect Daemon Logs (⌘L)."
        )
    }

    nonisolated private static func verifyPythonImports(pythonPath: String) -> Bool {
        let proc = Process()
        proc.executableURL = URL(fileURLWithPath: pythonPath)
        proc.arguments = ["-c", "import fastapi, uvicorn, asyncssh, cryptography"]
        proc.standardOutput = Pipe()
        proc.standardError = Pipe()
        do {
            try proc.run()
            proc.waitUntilExit()
            return proc.terminationStatus == 0
        } catch {
            return false
        }
    }

    nonisolated private static func verifyPythonVersionAtLeast310(pythonPath: String) -> Bool {
        let proc = Process()
        proc.executableURL = URL(fileURLWithPath: pythonPath)
        proc.arguments = ["-c", "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)"]
        proc.standardOutput = Pipe()
        proc.standardError = Pipe()
        do {
            try proc.run()
            proc.waitUntilExit()
            return proc.terminationStatus == 0
        } catch {
            return false
        }
    }

    nonisolated private static func runAndCapture(executable: String, arguments: [String]) -> String? {
        let proc = Process()
        proc.executableURL = URL(fileURLWithPath: executable)
        proc.arguments = arguments
        let pipe = Pipe()
        proc.standardOutput = pipe
        proc.standardError = Pipe()
        do {
            try proc.run()
            proc.waitUntilExit()
            guard proc.terminationStatus == 0 else { return nil }
            let data = pipe.fileHandleForReading.readDataToEndOfFile()
            return String(data: data, encoding: .utf8)
        } catch {
            return nil
        }
    }

    nonisolated private static func runLoggedCommand(executable: String, arguments: [String], logPath: String) -> Int32 {
        let proc = Process()
        proc.executableURL = URL(fileURLWithPath: executable)
        proc.arguments = arguments
        let fm = FileManager.default
        if !fm.fileExists(atPath: logPath) {
            fm.createFile(atPath: logPath, contents: nil, attributes: [.posixPermissions: 0o644])
        }
        if let handle = try? FileHandle(forWritingTo: URL(fileURLWithPath: logPath)) {
            handle.seekToEndOfFile()
            proc.standardOutput = handle
            proc.standardError = handle
            defer { try? handle.close() }
            do {
                try proc.run()
                proc.waitUntilExit()
                return proc.terminationStatus
            } catch {
                return -1
            }
        }
        return -1
    }

    nonisolated private static func appendDiagnosticLog(logPath: String, message: String) {
        let fm = FileManager.default
        if !fm.fileExists(atPath: logPath) {
            fm.createFile(atPath: logPath, contents: nil, attributes: [.posixPermissions: 0o644])
        }
        guard let handle = try? FileHandle(forWritingTo: URL(fileURLWithPath: logPath)),
              let data = "\(message)\n".data(using: .utf8) else { return }
        handle.seekToEndOfFile()
        handle.write(data)
        try? handle.close()
    }
}
