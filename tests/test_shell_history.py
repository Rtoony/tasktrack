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


def test_record_deep_link_is_parsed_and_opens_drawer(auth_client):
    """W3: ?record=<id> deep-links must be parsed at startup and open the
    record drawer for the active tab. JS behavior can't run headless here, so
    assert the served SPA wires the parser + open path that a browser executes.
    """
    res = auth_client.get("/")
    assert res.status_code == 200
    html = res.get_data(as_text=True)

    # ?record= is read once, alongside the existing ?tab= parsing.
    assert "record: params.get('record') || ''," in html
    # The active-tab slug is resolved from either a slug or a raw table name.
    assert "function deepLinkTabSlug(value)" in html
    # The drawer is opened via the canonical fetch + openModal path.
    assert "async function applyRecordDeepLink(slug, rawId)" in html
    assert "openModal(slug, data)" in html
    # The deep-link is honored from the startup route handler.
    assert "if (state.record) await applyRecordDeepLink(deepSlug, state.record);" in html
