import Foundation

struct AccountSummary: Codable, Identifiable, Equatable, Sendable {
    let id: String
    let email: String
    let display_name: String?
    let ccu_balance: Double
    let burn_rate_hourly: Double
    let active_assignments: Int
    let warning_ccu: Double
    let hard_stop_ccu: Double
    let priority: Int
    let status: String
    let last_synced_at: String?
}

struct SessionSummary: Codable, Identifiable, Equatable, Sendable {
    let id: String
    let name: String
    let account_id: String
    let account_email: String?
    let notebook_hash: String
    let endpoint: String
    let proxy_url: String
    let accelerator: String
    let shape: String
    let lifecycle_policy: String
    let idle_timeout_sec: Int
    let ssh_local_port: Int?
    let workspace_subpath: String
    let remote_workdir: String
    let template_id: String?
    let snapshot_id: String?
    let status: String
    let created_at: String
    let stopped_at: String?
    let colab_web_url: String?

    var isRunning: Bool {
        switch status.uppercased() {
        case "PROVISIONING", "BOOTSTRAPPING", "READY", "BUSY", "ORPHAN":
            return true
        default:
            return false
        }
    }

    var isReady: Bool {
        switch status.uppercased() {
        case "READY", "BUSY", "ORPHAN":
            return true
        default:
            return false
        }
    }

    var sshCommand: String? {
        guard let port = ssh_local_port, port > 0 else { return nil }
        return "ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -p \(port) root@localhost"
    }
}

struct LoginRequestPayload: Codable, Sendable {
    let password: String
}

struct LoginResponse: Codable, Sendable {
    let token: String
    let token_type: String?
}

struct DatabaseHealthPayload: Codable, Sendable {
    let status: String?
    let path: String?
}

struct HealthzResponse: Codable, Sendable {
    let status: String
    let service: String?
    let port: Int?
    let database: DatabaseHealthPayload?
}

enum DaemonBootPhase: Int, Comparable, Sendable {
    case probing = 0
    case resolvingPython = 1
    case launchingDaemon = 2
    case authenticating = 3
    case ready = 4

    static func < (lhs: DaemonBootPhase, rhs: DaemonBootPhase) -> Bool {
        lhs.rawValue < rhs.rawValue
    }

    var title: String {
        switch self {
        case .probing:
            return "Probing local control plane (:1113)"
        case .resolvingPython:
            return "Verifying Apple Silicon Python runtime"
        case .launchingDaemon:
            return "Starting FastAPI + SSH Multiplexer"
        case .authenticating:
            return "Establishing local admin vault session"
        case .ready:
            return "Control plane online"
        }
    }
}

enum NativeIDETarget: String, Sendable {
    case cursor
    case vscode

    var scheme: String {
        switch self {
        case .cursor: return "cursor"
        case .vscode: return "vscode"
        }
    }

    var displayName: String {
        switch self {
        case .cursor: return "Cursor"
        case .vscode: return "VS Code"
        }
    }
}

struct NativeCommandDispatch: Sendable {
    let command: String
    let payload: [String: String]

    init(command: String, payload: [String: String] = [:]) {
        self.command = command
        self.payload = payload
    }
}
