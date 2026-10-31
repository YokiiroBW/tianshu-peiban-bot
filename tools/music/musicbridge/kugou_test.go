package main

import (
	"io"
	"net/http"
	"strings"
	"testing"

	"github.com/guohuiyuan/music-lib/model"
)

func TestKugouWebQRUsesAuthenticatedFallbackButStopsOnRateLimit(t *testing.T) {
	previous := http.DefaultTransport
	defer func() { http.DefaultTransport = previous }()
	for _, rateLimited := range []bool{false, true} {
		webCalls := 0
		http.DefaultTransport = roundTripFunc(func(r *http.Request) (*http.Response, error) {
			body := `{}`
			switch r.URL.Hostname() {
			case "m.kugou.com":
				body = `{"errcode":0,"url":""}`
				if rateLimited {
					body = `{"errcode":1002,"url":""}`
				}
			case "wwwapi.kugou.com":
				webCalls++
				if r.URL.Query().Get("token") != "synthetic" {
					t.Fatal("lost web account credential")
				}
				body = `{"status":1,"data":{"encode_album_audio_id":"fixture","song_name":"Fixture"}}`
				if r.URL.Query().Get("encode_album_audio_id") != "" {
					body = `{"status":1,"data":{"play_url":"http://fs.example.kugou.com/fixture.mp3","timelength":180000}}`
				}
			}
			return &http.Response{StatusCode: 200, Header: make(http.Header), Body: io.NopCloser(strings.NewReader(body))}, nil
		})
		_, err := resolveKugou(request{Provider: "kugou", Cookie: "t=synthetic; KugooID=123", Level: "standard", Song: model.Song{Source: "kugou", ID: strings.Repeat("a", 32)}})
		if rateLimited {
			if err == nil || errorCode(err) != "music_rate_limited" || webCalls != 0 {
				t.Fatal("rate limit must stop fallback")
			}
		} else if err != nil || webCalls != 2 {
			t.Fatalf("web fallback: calls=%d err=%v", webCalls, err)
		}
	}
}
