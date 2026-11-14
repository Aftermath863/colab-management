// swift-tools-version: 5.10

import PackageDescription

let package = Package(
    name: "ColabManager",
    platforms: [
        .macOS(.v14)
    ],
    products: [
        .executable(
            name: "ColabManager",
            targets: ["ColabManager"]
        )
    ],
    targets: [
        .executableTarget(
            name: "ColabManager",
            path: "Sources/ColabManager",
            linkerSettings: [
                .linkedFramework("AppKit"),
                .linkedFramework("SwiftUI"),
                .linkedFramework("WebKit"),
                .linkedFramework("UserNotifications"),
                .linkedFramework("Network"),
                .linkedFramework("Combine")
            ]
        )
    ]
)
