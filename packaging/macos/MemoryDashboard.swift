import AppKit
import Foundation
import WebKit

private struct LauncherConfig: Decodable {
    let schema_version: Int
    let python_executable: String
    let skill_root: String
    let archive_root: String
}

private enum LauncherError: LocalizedError {
    case invalidConfig(String)
    case serverUnavailable

    var errorDescription: String? {
        switch self {
        case .invalidConfig(let detail):
            return "Invalid Memory Wuxian dashboard configuration: \(detail)"
        case .serverUnavailable:
            return "The local Memory Wuxian dashboard server did not become available."
        }
    }
}

private let dashboardURL = URL(string: "http://127.0.0.1:8765/")!
private let statusURL = URL(string: "http://127.0.0.1:8765/api/status")!

private func configURL() -> URL {
    if let configured = ProcessInfo.processInfo.environment[
        "MEMORY_WUXIAN_DASHBOARD_CONFIG"
    ], configured.hasPrefix("/") {
        return URL(fileURLWithPath: configured)
    }
    return FileManager.default.homeDirectoryForCurrentUser
        .appendingPathComponent(".codex/memory-wuxian-dashboard-launcher.json")
}

private func loadConfig() throws -> LauncherConfig {
    let config = try JSONDecoder().decode(
        LauncherConfig.self,
        from: Data(contentsOf: configURL())
    )
    guard config.schema_version == 1 else {
        throw LauncherError.invalidConfig("unsupported schema version")
    }
    let checks = [
        ("Python runtime", config.python_executable, false),
        ("Skill root", config.skill_root, true),
        ("archive root", config.archive_root, true),
    ]
    for (label, path, directory) in checks {
        guard path.hasPrefix("/") else {
            throw LauncherError.invalidConfig("\(label) is not absolute")
        }
        var isDirectory: ObjCBool = false
        guard FileManager.default.fileExists(atPath: path, isDirectory: &isDirectory),
              isDirectory.boolValue == directory else {
            throw LauncherError.invalidConfig("\(label) does not exist")
        }
    }
    let dashboard = URL(fileURLWithPath: config.skill_root)
        .appendingPathComponent("scripts/memory_dashboard.py").path
    let skillConfig = URL(fileURLWithPath: config.skill_root)
        .appendingPathComponent("config.yaml").path
    guard FileManager.default.isExecutableFile(atPath: config.python_executable),
          FileManager.default.fileExists(atPath: dashboard),
          FileManager.default.fileExists(atPath: skillConfig) else {
        throw LauncherError.invalidConfig("required runtime files are missing")
    }
    return config
}

private func serverIsReady() -> Bool {
    var request = URLRequest(url: statusURL)
    request.timeoutInterval = 1
    let semaphore = DispatchSemaphore(value: 0)
    var ready = false
    URLSession.shared.dataTask(with: request) { _, response, _ in
        if let http = response as? HTTPURLResponse, http.statusCode == 200 {
            ready = true
        }
        semaphore.signal()
    }.resume()
    _ = semaphore.wait(timeout: .now() + 2)
    return ready
}

private func appendLog(_ message: String) {
    let log = FileManager.default.homeDirectoryForCurrentUser
        .appendingPathComponent("Library/Logs/MemoryWuxian/dashboard.log")
    try? FileManager.default.createDirectory(
        at: log.deletingLastPathComponent(),
        withIntermediateDirectories: true
    )
    let line = "\(ISO8601DateFormatter().string(from: Date())) \(message)\n"
    if let handle = try? FileHandle(forWritingTo: log) {
        defer { try? handle.close() }
        _ = try? handle.seekToEnd()
        try? handle.write(contentsOf: Data(line.utf8))
    } else {
        try? Data(line.utf8).write(to: log, options: .atomic)
    }
}

private func selfCheck() -> Int32 {
    do {
        let config = try loadConfig()
        let payload: [String: Any] = [
            "status": "ok",
            "schema_version": config.schema_version,
            "python_executable": config.python_executable,
            "skill_root": config.skill_root,
            "archive_root": config.archive_root,
        ]
        let data = try JSONSerialization.data(
            withJSONObject: payload,
            options: [.prettyPrinted, .sortedKeys]
        )
        FileHandle.standardOutput.write(data)
        FileHandle.standardOutput.write(Data("\n".utf8))
        return 0
    } catch {
        FileHandle.standardError.write(Data("\(error.localizedDescription)\n".utf8))
        return 1
    }
}

private final class DashboardDelegate: NSObject, NSApplicationDelegate, NSWindowDelegate {
    private var window: NSWindow?
    private var server: Process?

    func applicationDidFinishLaunching(_ notification: Notification) {
        do {
            let config = try loadConfig()
            createWindow()
            DispatchQueue.global(qos: .userInitiated).async {
                do {
                    if !serverIsReady() {
                        try self.startServer(config)
                    }
                    for _ in 0..<80 {
                        if serverIsReady() {
                            DispatchQueue.main.async {
                                self.window?.contentView.flatMap { $0 as? WKWebView }?
                                    .load(URLRequest(url: dashboardURL))
                                self.window?.makeKeyAndOrderFront(nil)
                                NSApp.activate(ignoringOtherApps: true)
                            }
                            return
                        }
                        Thread.sleep(forTimeInterval: 0.1)
                    }
                    throw LauncherError.serverUnavailable
                } catch {
                    self.fail(error)
                }
            }
        } catch {
            fail(error)
        }
    }

    private func createWindow() {
        let webView = WKWebView(frame: .zero)
        let window = NSWindow(
            contentRect: NSRect(x: 0, y: 0, width: 1180, height: 760),
            styleMask: [.titled, .closable, .miniaturizable, .resizable],
            backing: .buffered,
            defer: false
        )
        window.title = "Memory無限操作台"
        window.center()
        window.minSize = NSSize(width: 760, height: 520)
        window.contentView = webView
        window.delegate = self
        self.window = window
    }

    private func startServer(_ config: LauncherConfig) throws {
        let process = Process()
        process.executableURL = URL(fileURLWithPath: config.python_executable)
        process.arguments = [
            URL(fileURLWithPath: config.skill_root)
                .appendingPathComponent("scripts/memory_dashboard.py").path,
            "--root", config.archive_root,
            "--config",
            URL(fileURLWithPath: config.skill_root).appendingPathComponent("config.yaml").path,
            "--no-browser",
            "--port", "8765",
        ]
        let log = FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent("Library/Logs/MemoryWuxian/dashboard-server.log")
        try FileManager.default.createDirectory(
            at: log.deletingLastPathComponent(),
            withIntermediateDirectories: true
        )
        FileManager.default.createFile(atPath: log.path, contents: nil)
        let handle = try FileHandle(forWritingTo: log)
        process.standardOutput = handle
        process.standardError = handle
        try process.run()
        server = process
        appendLog("started dashboard server pid=\(process.processIdentifier)")
    }

    private func fail(_ error: Error) {
        appendLog("launch failed: \(error.localizedDescription)")
        DispatchQueue.main.async {
            let alert = NSAlert()
            alert.messageText = "Memory無限操作台无法启动"
            alert.informativeText = error.localizedDescription
            alert.alertStyle = .critical
            alert.runModal()
            NSApp.terminate(nil)
        }
    }

    func windowWillClose(_ notification: Notification) {
        NSApp.terminate(nil)
    }

    func applicationWillTerminate(_ notification: Notification) {
        if let server, server.isRunning {
            server.terminate()
        }
    }
}

if CommandLine.arguments.contains("--self-check") {
    exit(selfCheck())
}

let application = NSApplication.shared
private let delegate = DashboardDelegate()
application.delegate = delegate
application.setActivationPolicy(.regular)
application.run()
