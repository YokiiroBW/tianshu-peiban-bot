"""Thin console routes; external reconciliation stays in the background owner."""

from ....contracts import Fault, require


async def route(web, operation, body, session):
    nav = web.media.music.navidrome
    state, local = nav.state, web.media.local
    fields = web.music.fields
    if operation == "test":
        fields(body, {"base_url", "username", "password"})
        connection = await local(state.connection, body)
        result = await nav.client.check(connection)
        await web.live(session)
        return result
    if operation == "save":
        fields(
            body,
            {
                "base_url",
                "username",
                "password",
                "music_folder_id",
                "music_directory",
                "expected_revision",
                "client_request_id",
            },
        )
        connection = await local(state.connection, body)
        safe = state.safe_command(body, connection)
        checked = await nav.client.check(connection)
        return await web.receipt(
            safe,
            "music/navidrome/save",
            session,
            lambda db: state.save(body, connection, checked, db),
        )
    if operation == "disable":
        fields(body, {"expected_revision", "client_request_id"})
        return await web.receipt(
            body, "music/navidrome/disable", session, lambda db: state.disable(body, db)
        )
    if operation == "playlists/save":
        fields(
            body, {"playlist_id", "name", "public", "auto_sync", "expected_revision", "client_request_id"}
        )
        return await web.receipt(
            body, "music/navidrome/playlists/save", session, lambda db: state.link(body, db)
        )
    if operation == "playlists/control":
        fields(body, {"link_id", "action", "expected_revision", "client_request_id"})
        require(isinstance(body["link_id"], str), "invalid_input", 400)
        return await web.receipt(
            body, "music/navidrome/playlists/control", session, lambda db: state.control(body, db)
        )
    raise Fault("not_found", 404)
