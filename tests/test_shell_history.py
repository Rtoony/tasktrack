"""Browser history affordances for the TaskTrack shell."""


def test_main_shell_wires_back_button_history(auth_client):
    res = auth_client.get("/")
    assert res.status_code == 200
    html = res.get_data(as_text=True)

    assert "function initShellHistory()" in html
    assert "window.addEventListener('popstate', handleShellPopState)" in html
    assert "history.pushState(shellHistoryState(initialTab" in html
    assert "Back is held inside TaskTrack" in html
    assert "function activateTab(tabName, options={})" in html
    assert "if (options.history !== false) pushShellHistory(currentTab);" in html
    assert "applyStartupRouteState().finally(initShellHistory);" in html


def test_deep_link_helpers_preserve_shell_history_state(auth_client):
    res = auth_client.get("/")
    assert res.status_code == 200
    html = res.get_data(as_text=True)

    assert "replaceShellHistoryState({ tab: 'map', mapProject: pn });" in html
    assert "replaceShellHistoryState({ tab: currentTab || 'dashboard', workspace: pn });" in html
    assert "if (state.tab && state.tab !== 'dashboard') activateTab(state.tab, { history: false });" in html
