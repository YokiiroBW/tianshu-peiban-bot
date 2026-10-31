# TianShu offline music decoder

Single staged-file JSON stdin/stdout wrapper around Unlock Music CLI v0.2.12,
licensed under MIT. Original source license and file hashes are in
`third_party/unlock_cli/LICENSE` and `UPSTREAM.json`.

The original `unlock-music.dev/mmkv v0.1.0` dependency is unavailable. The vendored
adaptation explicitly rejects external MMKV vault access instead of pretending
those keys exist. Self-contained NCM/QMC and supported KGM algorithms retain the
upstream implementation. KGG requiring a client database is unavailable here.
Unneeded upstream command/tests are omitted. No official client binaries or keys
are included, and the wrapper's HTTP transport denies network requests.

Build: `go build -trimpath -mod=readonly -o tianshu-musicdecode .` using Go 1.27.2.
Version request: `{"operation":"version"}`. Decode request contains absolute
`input`, new `output`, and optional new `cover` paths, all prepared by the media domain.
It never deletes the original. The media domain's shared automatic acquisition/import
pipeline detects encrypted headers or source filename hints, supplies a private
staging copy with the correct suffix, and verifies the entire actual audio stream
before tagging and publishing. Ordinary audio bypasses the decoder.

Missing-key QMC STag/MusicEx and KGM v5 return `music_decode_key_required`;
unknown KGM versions return `music_decode_unsupported`. Invalid inputs return
`music_decode_failed`. The wrapper bounds NCM frames and validates KGM v3
audio offsets before invoking upstream code. No missing vault or KGG key is
invented. A failed download candidate may fall back to another entitled source.

The Docker build packages corresponding helper source and vendored dependencies.
Python integration tests generate synthetic NCM from a sine wave and exercise
the real executable, tags, shared recording identity, reuse and corrupt-input
retention. QMC synthetic OGG exercises real decoding and portable Vorbis tags;
Opus bypass retains the original codec. Go checks QMC byte preservation and
external-key/version error classification. This is not verification against the
user's encrypted music files.
