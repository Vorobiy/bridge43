// swift-tools-version:5.9
import PackageDescription

let package = Package(
    name: "Bridge43Recorder",
    platforms: [.macOS("14.4")],
    targets: [
        .executableTarget(
            name: "bridge43-recorder",
            path: "Sources/Bridge43Recorder",
            linkerSettings: [
                .linkedFramework("ScreenCaptureKit"),
                .linkedFramework("CoreAudio"),
                .linkedFramework("AVFoundation"),
                .linkedLibrary("sqlite3"),
            ]
        )
    ]
)
