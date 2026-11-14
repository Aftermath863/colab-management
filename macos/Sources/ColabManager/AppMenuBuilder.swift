import AppKit

@MainActor
protocol AppMenuActions: AnyObject {
    func showMainWindowAction(_ sender: Any?)
    func showDaemonLogsAction(_ sender: Any?)
    func revealWorkspaceAction(_ sender: Any?)
    func revealDataDirAction(_ sender: Any?)
    func reloadWorkbenchAction(_ sender: Any?)
    func fleetCommandPaletteAction(_ sender: Any?)
    func fleetNewSessionAction(_ sender: Any?)
    func fleetLinkAccountAction(_ sender: Any?)
    func fleetSyncAccountsAction(_ sender: Any?)
    func navigateOverviewAction(_ sender: Any?)
    func navigateSessionsAction(_ sender: Any?)
    func navigateJobsAction(_ sender: Any?)
    func navigateAccountsAction(_ sender: Any?)
    func navigateTemplatesAction(_ sender: Any?)
    func navigateNotificationsAction(_ sender: Any?)
}

@MainActor
enum AppMenuBuilder {
    static func buildMainMenu(target: AnyObject) -> NSMenu {
        let mainMenu = NSMenu()

        // 1. Application Menu
        let appMenuItem = NSMenuItem()
        mainMenu.addItem(appMenuItem)
        let appMenu = NSMenu(title: "Colab Manager")
        appMenuItem.submenu = appMenu

        appMenu.addItem(withTitle: "About Colab Manager", action: #selector(NSApplication.orderFrontStandardAboutPanel(_:)), keyEquivalent: "")
        appMenu.addItem(.separator())

        let logsItem = NSMenuItem(title: "Daemon Logs…", action: #selector(AppDelegate.showDaemonLogsAction(_:)), keyEquivalent: "l")
        logsItem.target = target
        appMenu.addItem(logsItem)

        let workspaceItem = NSMenuItem(title: "Open Workspace in Finder", action: #selector(AppDelegate.revealWorkspaceAction(_:)), keyEquivalent: "")
        workspaceItem.target = target
        appMenu.addItem(workspaceItem)

        let dataDirItem = NSMenuItem(title: "Open Data Folder in Finder", action: #selector(AppDelegate.revealDataDirAction(_:)), keyEquivalent: "")
        dataDirItem.target = target
        appMenu.addItem(dataDirItem)

        appMenu.addItem(.separator())
        let servicesItem = NSMenuItem(title: "Services", action: nil, keyEquivalent: "")
        let servicesMenu = NSMenu(title: "Services")
        servicesItem.submenu = servicesMenu
        NSApp.servicesMenu = servicesMenu
        appMenu.addItem(servicesItem)

        appMenu.addItem(.separator())
        appMenu.addItem(withTitle: "Hide Colab Manager", action: #selector(NSApplication.hide(_:)), keyEquivalent: "h")

        let hideOthersItem = NSMenuItem(title: "Hide Others", action: #selector(NSApplication.hideOtherApplications(_:)), keyEquivalent: "h")
        hideOthersItem.keyEquivalentModifierMask = [.command, .option]
        appMenu.addItem(hideOthersItem)

        appMenu.addItem(withTitle: "Show All", action: #selector(NSApplication.unhideAllApplications(_:)), keyEquivalent: "")
        appMenu.addItem(.separator())
        appMenu.addItem(withTitle: "Quit Colab Manager", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")

        // 2. Edit Menu (essential for Monaco & xterm clipboard + undo/redo)
        let editMenuItem = NSMenuItem()
        mainMenu.addItem(editMenuItem)
        let editMenu = NSMenu(title: "Edit")
        editMenuItem.submenu = editMenu

        editMenu.addItem(withTitle: "Undo", action: Selector(("undo:")), keyEquivalent: "z")
        let redoItem = NSMenuItem(title: "Redo", action: Selector(("redo:")), keyEquivalent: "z")
        redoItem.keyEquivalentModifierMask = [.command, .shift]
        editMenu.addItem(redoItem)
        editMenu.addItem(.separator())
        editMenu.addItem(withTitle: "Cut", action: #selector(NSText.cut(_:)), keyEquivalent: "x")
        editMenu.addItem(withTitle: "Copy", action: #selector(NSText.copy(_:)), keyEquivalent: "c")
        editMenu.addItem(withTitle: "Paste", action: #selector(NSText.paste(_:)), keyEquivalent: "v")
        editMenu.addItem(withTitle: "Delete", action: #selector(NSText.delete(_:)), keyEquivalent: "")
        editMenu.addItem(withTitle: "Select All", action: #selector(NSText.selectAll(_:)), keyEquivalent: "a")

        // 3. Fleet Menu
        let fleetMenuItem = NSMenuItem()
        mainMenu.addItem(fleetMenuItem)
        let fleetMenu = NSMenu(title: "Fleet")
        fleetMenuItem.submenu = fleetMenu

        let paletteItem = NSMenuItem(title: "Command Palette…", action: #selector(AppDelegate.fleetCommandPaletteAction(_:)), keyEquivalent: "k")
        paletteItem.target = target
        fleetMenu.addItem(paletteItem)

        let newSessionItem = NSMenuItem(title: "New Runtime…", action: #selector(AppDelegate.fleetNewSessionAction(_:)), keyEquivalent: "n")
        newSessionItem.target = target
        fleetMenu.addItem(newSessionItem)

        let linkAccountItem = NSMenuItem(title: "Link Google Account…", action: #selector(AppDelegate.fleetLinkAccountAction(_:)), keyEquivalent: "")
        linkAccountItem.target = target
        fleetMenu.addItem(linkAccountItem)

        let syncAccountsItem = NSMenuItem(title: "Sync Account Quotas", action: #selector(AppDelegate.fleetSyncAccountsAction(_:)), keyEquivalent: "r")
        syncAccountsItem.keyEquivalentModifierMask = [.command, .shift]
        syncAccountsItem.target = target
        fleetMenu.addItem(syncAccountsItem)

        fleetMenu.addItem(.separator())

        let navEntries: [(String, Selector, String)] = [
            ("Overview", #selector(AppDelegate.navigateOverviewAction(_:)), "1"),
            ("Compute Sessions", #selector(AppDelegate.navigateSessionsAction(_:)), "2"),
            ("Batch Jobs", #selector(AppDelegate.navigateJobsAction(_:)), "3"),
            ("Google Accounts", #selector(AppDelegate.navigateAccountsAction(_:)), "4"),
            ("Templates & Vault", #selector(AppDelegate.navigateTemplatesAction(_:)), "5"),
            ("Notifications", #selector(AppDelegate.navigateNotificationsAction(_:)), "6")
        ]
        for (title, selector, key) in navEntries {
            let item = NSMenuItem(title: title, action: selector, keyEquivalent: key)
            item.target = target
            fleetMenu.addItem(item)
        }

        fleetMenu.addItem(.separator())
        let reloadItem = NSMenuItem(title: "Reload Workbench", action: #selector(AppDelegate.reloadWorkbenchAction(_:)), keyEquivalent: "r")
        reloadItem.target = target
        fleetMenu.addItem(reloadItem)

        // 4. Window Menu
        let windowMenuItem = NSMenuItem()
        mainMenu.addItem(windowMenuItem)
        let windowMenu = NSMenu(title: "Window")
        windowMenuItem.submenu = windowMenu
        NSApp.windowsMenu = windowMenu

        let showWorkbenchItem = NSMenuItem(title: "Main Workbench", action: #selector(AppDelegate.showMainWindowAction(_:)), keyEquivalent: "")
        showWorkbenchItem.target = target
        windowMenu.addItem(showWorkbenchItem)

        let showLogsItem = NSMenuItem(title: "Daemon Logs", action: #selector(AppDelegate.showDaemonLogsAction(_:)), keyEquivalent: "")
        showLogsItem.target = target
        windowMenu.addItem(showLogsItem)

        windowMenu.addItem(.separator())
        windowMenu.addItem(withTitle: "Minimize", action: #selector(NSWindow.performMiniaturize(_:)), keyEquivalent: "m")
        windowMenu.addItem(withTitle: "Zoom", action: #selector(NSWindow.performZoom(_:)), keyEquivalent: "")

        let fullScreenItem = NSMenuItem(title: "Toggle Full Screen", action: #selector(NSWindow.toggleFullScreen(_:)), keyEquivalent: "f")
        fullScreenItem.keyEquivalentModifierMask = [.command, .control]
        windowMenu.addItem(fullScreenItem)

        windowMenu.addItem(withTitle: "Close Window", action: #selector(NSWindow.performClose(_:)), keyEquivalent: "w")
        windowMenu.addItem(.separator())
        windowMenu.addItem(withTitle: "Bring All to Front", action: #selector(NSApplication.arrangeInFront(_:)), keyEquivalent: "")

        return mainMenu
    }
}
