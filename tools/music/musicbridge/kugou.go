// SPDX-License-Identifier: AGPL-3.0-or-later
package main

import (
	"errors"

	"github.com/guohuiyuan/music-lib/kugou"
)

func resolveKugou(r request) (any, error) {
	client := kugou.New(r.Cookie)
	link, err := client.GetDownloadURL(&r.Song)
	if err != nil && (errorCode(err) == "music_rate_limited" || errorCode(err) == "music_auth_required") {
		return nil, err
	}
	// Web QR credentials also have a signed, authenticated songinfo path.
	// The mobile endpoint can return an empty URL for the same hash. Reuse the
	// pinned adapter, with the same account and selected tier, without guessing URLs.
	if (err != nil || link == "") && cookieValue(r.Cookie, "t") != "" && cookieValue(r.Cookie, "KugooID") != "" {
		fallback, fallbackErr := client.GetDownloadURLBySonginfo(&r.Song)
		if fallbackErr == nil && fallback != "" {
			link, err = fallback, nil
		} else if fallbackErr != nil && (errorCode(fallbackErr) == "music_rate_limited" || errorCode(fallbackErr) == "music_auth_required") {
			return nil, fallbackErr
		}
	}
	if err != nil {
		return nil, err
	}
	if link == "" {
		return nil, errors.New("download forbidden")
	}
	return map[string]any{"url": link, "level": "observed", "size": nil, "md5": nil}, nil
}
