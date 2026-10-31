// SPDX-License-Identifier: AGPL-3.0-or-later
// Local, one-request adapter. Credentials are read from stdin, never command arguments.
package main

import (
	"bytes"
	"encoding/base64"
	"encoding/json"
	"errors"
	"io"
	"log"
	"net/http"
	"net/url"
	"os"
	"regexp"
	"runtime/debug"
	"strconv"
	"strings"
	"time"

	"github.com/guohuiyuan/music-lib/kugou"
	"github.com/guohuiyuan/music-lib/model"
	"github.com/guohuiyuan/music-lib/provider"
	"github.com/guohuiyuan/music-lib/qq"
)

const sourceRevision = "deb29cf7c49051df9744d788e2a573b280b300f2"

type request struct {
	Provider          string     `json:"provider"`
	Operation         string     `json:"operation"`
	Cookie            string     `json:"cookie"`
	ID                string     `json:"id"`
	Key               string     `json:"key"`
	Query             string     `json:"query"`
	Level             string     `json:"level"`
	LoginType         string     `json:"login_type"`
	Page              int        `json:"page"`
	Limit             int        `json:"limit"`
	Song              model.Song `json:"song"`
	RequestLimit      int        `json:"request_limit"`
	RequestIntervalMS int        `json:"request_interval_ms"`
}

type client interface {
	provider.MusicProvider
	provider.PlaylistProvider
	provider.UserPlaylistProvider
	provider.QRLoginProvider
}

type officialTransport struct {
	next   http.RoundTripper
	pacing *requestPacing
}

func (t officialTransport) RoundTrip(r *http.Request) (*http.Response, error) {
	host := strings.ToLower(r.URL.Hostname())
	allowed := false
	for _, suffix := range []string{"qq.com", "weixin.qq.com", "gtimg.cn", "kugou.com", "kugou.net"} {
		if host == suffix || strings.HasSuffix(host, "."+suffix) {
			allowed = true
		}
	}
	if !allowed || r.URL.User != nil {
		return nil, errors.New("unsupported upstream host")
	}
	clone := r.Clone(r.Context())
	target := *r.URL
	target.Scheme = "https"
	clone.URL = &target
	if t.pacing != nil {
		if err := t.pacing.before(clone); err != nil {
			return nil, err
		}
	}
	response, err := t.next.RoundTrip(clone)
	if err == nil {
		if t.pacing != nil {
			if blocked := t.pacing.observe(response); blocked != nil {
				response.Body.Close()
				return nil, blocked
			}
		}
		response.Body = &limitedBody{Reader: io.LimitReader(response.Body, 32<<20), close: response.Body}
	}
	return response, err
}

type limitedBody struct {
	io.Reader
	close io.Closer
}

func (b *limitedBody) Close() error { return b.close.Close() }

func cookieValue(cookie, key string) string {
	for _, pair := range strings.Split(cookie, ";") {
		value := strings.SplitN(strings.TrimSpace(pair), "=", 2)
		if len(value) == 2 && value[0] == key {
			return value[1]
		}
	}
	return ""
}

func userID(r request) string {
	keys := []string{"musicid", "uin", "qqmusic_uin", "wxuin"}
	if r.Provider == "kugou" {
		keys = []string{"userid", "KugooID"}
	}
	for _, key := range keys {
		id := strings.TrimLeft(cookieValue(r.Cookie, key), "o0")
		if regexp.MustCompile(`^[1-9][0-9]{0,24}$`).MatchString(id) {
			return id
		}
	}
	return ""
}

func resolveQQ(r request) (any, error) {
	prefixes := map[string][2]string{
		"standard": {"M500", "mp3"}, "higher": {"M800", "mp3"},
		"exhigh": {"M800", "mp3"}, "lossless": {"F000", "flac"},
		"hires": {"AI00", "flac"}, "best": {"AI00", "flac"},
	}
	selected, ok := prefixes[r.Level]
	if !ok {
		return nil, errors.New("invalid quality")
	}
	mid := r.Song.ID
	if r.Song.Extra["songmid"] != "" {
		mid = r.Song.Extra["songmid"]
	}
	if !regexp.MustCompile(`^[A-Za-z0-9]+$`).MatchString(mid) {
		return nil, errors.New("invalid song")
	}
	filename := selected[0] + mid + mid + "." + selected[1]
	if media := r.Song.Extra["media_mid"]; media != "" {
		filename = selected[0] + media + "." + selected[1]
	}
	data := map[string]any{
		"comm": map[string]any{"ct": 24, "cv": 4747474, "uin": userID(r), "format": "json"},
		"req_1": map[string]any{"module": "music.vkey.GetVkey", "method": "UrlGetVkey", "param": map[string]any{
			"guid":    strconv.FormatInt(time.Now().UnixNano()%9000000000+1000000000, 10),
			"songmid": []string{mid}, "songtype": []int{0}, "filename": []string{filename},
			"uin": userID(r), "loginflag": 1, "platform": "20", "ctx": 0,
		}},
	}
	raw, _ := json.Marshal(data)
	req, err := http.NewRequest(http.MethodPost, "https://u.y.qq.com/cgi-bin/musicu.fcg", bytes.NewReader(raw))
	if err != nil {
		return nil, err
	}
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Referer", "https://y.qq.com/")
	req.Header.Set("Cookie", r.Cookie)
	req.Header.Set("User-Agent", "Mozilla/5.0")
	response, err := http.DefaultClient.Do(req)
	if err != nil {
		return nil, err
	}
	defer response.Body.Close()
	var value struct {
		Req struct {
			Code int `json:"code"`
			Data struct {
				URLs []struct {
					Purl     string `json:"purl"`
					Filename string `json:"filename"`
				} `json:"midurlinfo"`
				SIP []string `json:"sip"`
			} `json:"data"`
		} `json:"req_1"`
	}
	if response.StatusCode != 200 {
		return nil, errors.New("upstream unavailable")
	}
	if err = json.NewDecoder(response.Body).Decode(&value); err != nil {
		return nil, err
	}
	if value.Req.Code != 0 {
		return nil, errors.New("download forbidden")
	}
	for _, row := range value.Req.Data.URLs {
		if row.Purl != "" && row.Filename == filename {
			base := "https://ws.stream.qqmusic.qq.com/"
			if len(value.Req.Data.SIP) > 0 {
				base = value.Req.Data.SIP[0]
			}
			origin, e := url.Parse(base)
			if e != nil {
				return nil, e
			}
			part, e := url.Parse(row.Purl)
			if e != nil {
				return nil, e
			}
			level := r.Level
			if level == "best" {
				level = "hires"
			}
			if level == "higher" {
				level = "exhigh"
			}
			return map[string]any{"url": origin.ResolveReference(part).String(), "level": level, "size": nil, "md5": nil}, nil
		}
	}
	return nil, errors.New("download forbidden")
}

func execute(r request) (any, error) {
	if r.Operation == "version" {
		return map[string]any{"protocol": 1, "music_lib_revision": sourceRevision, "providers": []string{"qq", "kugou"}}, nil
	}
	var c client
	switch r.Provider {
	case "qq":
		c = qq.New(r.Cookie)
	case "kugou":
		c = kugou.New(r.Cookie)
	default:
		return nil, errors.New("unsupported provider")
	}
	switch r.Operation {
	case "qr_start":
		var qr *model.QRLoginSession
		var err error
		if r.Provider == "qq" {
			qr, err = qq.New(r.Cookie).CreateQRLoginByType(r.LoginType)
		} else {
			qr, err = c.CreateQRLogin()
		}
		if err != nil {
			return nil, err
		}
		if strings.HasPrefix(qr.ImageURL, "https://") {
			response, e := http.Get(qr.ImageURL)
			if e != nil {
				return nil, e
			}
			defer response.Body.Close()
			image, e := io.ReadAll(io.LimitReader(response.Body, 2<<20))
			if e != nil || response.StatusCode != 200 {
				return nil, errors.New("qr image unavailable")
			}
			mime := http.DetectContentType(image)
			if mime != "image/png" && mime != "image/jpeg" {
				return nil, errors.New("invalid qr image")
			}
			qr.ImageURL = "data:" + mime + ";base64," + base64.StdEncoding.EncodeToString(image)
		}
		return qr, nil
	case "qr_poll":
		if r.Provider == "qq" {
			return qq.New(r.Cookie).CheckQRLoginByType(r.LoginType, r.Key)
		}
		return c.CheckQRLogin(r.Key)
	case "account":
		id := userID(r)
		if id == "" {
			return nil, errors.New("authentication required")
		}
		_, err := c.GetUserPlaylists(1, 1)
		if err != nil {
			return nil, err
		}
		return map[string]any{"user_id": id, "label": strings.ToUpper(r.Provider) + " " + id}, nil
	case "playlists":
		return c.GetUserPlaylists(r.Page, r.Limit)
	case "playlist_songs":
		return c.GetPlaylistSongs(r.ID)
	case "playlist":
		return playlist(c, r.ID)
	case "search":
		if r.Provider == "qq" {
			return searchQQ(r)
		}
		return c.Search(r.Query)
	case "track":
		return c.Parse(r.ID)
	case "lyrics":
		return c.GetLyrics(&r.Song)
	case "download":
		if r.Provider == "qq" {
			return resolveQQ(r)
		}
		return resolveKugou(r)
	}
	return nil, errors.New("unsupported operation")
}

func playlist(c client, id string) (any, error) {
	item, songs, err := c.ParsePlaylist(id)
	if err != nil {
		return nil, err
	}
	return map[string]any{"playlist": item, "songs": songs}, nil
}

func errorCode(err error) string {
	value := strings.ToLower(err.Error())
	if strings.Contains(value, "kugou song info unavailable, errcode=1002") {
		return "music_rate_limited"
	}
	if strings.Contains(value, "kugou song info unavailable, errcode=0") {
		return "music_quality_unavailable"
	}
	if strings.Contains(value, "rate limited") || strings.Contains(value, "too many requests") {
		return "music_rate_limited"
	}
	if strings.Contains(value, "forbidden") || strings.Contains(value, "vip required") || strings.Contains(value, "no valid download") {
		return "music_download_forbidden"
	}
	if strings.Contains(value, "require cookie") || strings.Contains(value, "authentication") || strings.Contains(value, "login expired") {
		return "music_auth_required"
	}
	if strings.Contains(value, "unsupported") || strings.Contains(value, "invalid") {
		return "music_upstream_invalid"
	}
	return "music_upstream_unavailable"
}

func main() {
	log.SetOutput(io.Discard)
	debug.SetMemoryLimit(256 << 20)
	http.DefaultClient.Timeout = 25 * time.Second
	output := map[string]any{}
	pacing := &requestPacing{limit: 32}
	defer func() {
		if recover() != nil {
			output = map[string]any{"ok": false, "code": "music_upstream_invalid"}
		}
		output["requests_used"] = pacing.used
		output["retry_after_seconds"] = pacing.retryAfter
		_ = json.NewEncoder(os.Stdout).Encode(output)
	}()
	var r request
	if err := json.NewDecoder(io.LimitReader(os.Stdin, 256<<10)).Decode(&r); err != nil {
		output["ok"] = false
		output["code"] = "invalid_input"
		return
	}
	if r.RequestLimit > 0 && r.RequestLimit <= 32 {
		pacing.limit = r.RequestLimit
	}
	if r.RequestIntervalMS > 0 && r.RequestIntervalMS <= 10000 {
		pacing.interval = time.Duration(r.RequestIntervalMS) * time.Millisecond
	}
	http.DefaultTransport = officialTransport{next: http.DefaultTransport, pacing: pacing}
	value, err := execute(r)
	if pacing.stopped != nil {
		err = pacing.stopped
	}
	if err != nil {
		output["ok"] = false
		output["code"] = errorCode(err)
		return
	}
	output["ok"] = true
	output["value"] = value
}
