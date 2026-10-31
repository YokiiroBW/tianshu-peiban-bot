# TianShu music bridge

A stateless, one-request executable for QQ Music and Kugou. JSON arrives on stdin;
only normalized results or fixed error codes go to stdout. It has no listener,
database, account store or background worker. The media domain owns credentials,
recording identity, per-track permission routing and downloaded audio.

Source: [guohuiyuan/music-lib](https://github.com/guohuiyuan/music-lib/tree/deb29cf7c49051df9744d788e2a573b280b300f2),
pinned as `v1.1.1-0.20261013052135-deb29cf7c490` in go.mod/go.sum.
This component uses the GNU Affero General Public License v3; see [LICENSE](LICENSE).
The independent decoder has its own license. Third-party licenses remain attached
to their respective components.

Build with Go 1.27.2: `go build -trimpath -mod=readonly -o tianshu-musicbridge .`.
Run `go test ./...` for QQ VKey filename/quality, unfiltered paid-song metadata
and account-subject contracts. Paid songs remain searchable; only the subsequent
VKey/full-audio acquisition decides entitlement.
`{"operation":"version"}` reports the pinned adapter revision without credentials.

The image contains binaries in `/usr/local/bin/` and the helper source, vendored
dependencies and build script in
`/usr/share/tianshu-music-tools/corresponding-source.tar.gz`.
`tools/music/build.py` pins and verifies the Go toolchain archive.
No proprietary desktop signing library is distributed. Soda is deliberately omitted.

Actual QQ/WeChat/Kugou QR generation and waiting states were checked on Windows.
User login, private playlists and entitled downloads need corresponding user accounts;
synthetic contract tests do not establish those live capabilities.
