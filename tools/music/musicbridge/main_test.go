package main

import (
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"strings"
	"testing"

	"github.com/guohuiyuan/music-lib/model"
)

func TestKugouQualityFailureDoesNotHideRateLimit(t *testing.T) {
	for _, sample := range []struct{ message, code string }{
		{"kugou song info unavailable, errcode=0", "music_quality_unavailable"},
		{"kugou song info unavailable, errcode=1002", "music_rate_limited"},
		{"kugou song info unavailable, errcode=2004", "music_upstream_unavailable"},
	} {
		if got := errorCode(errors.New(sample.message)); got != sample.code {
			t.Fatalf("got %s, want %s", got, sample.code)
		}
	}
}

type roundTripFunc func(*http.Request) (*http.Response, error)

func (f roundTripFunc) RoundTrip(r *http.Request) (*http.Response, error) { return f(r) }

func TestQQVKeyUsesRequestedQualityAndStableSongIdentity(t *testing.T) {
	previous := http.DefaultClient
	defer func() { http.DefaultClient = previous }()
	http.DefaultClient = &http.Client{Transport: roundTripFunc(func(r *http.Request) (*http.Response, error) {
		raw, _ := io.ReadAll(r.Body)
		var body map[string]any
		if err := json.Unmarshal(raw, &body); err != nil {
			t.Fatal(err)
		}
		// Reject accidental use of the display title or a guessed media MID.
		if !strings.Contains(string(raw), "F000songmid101songmid101.flac") {
			t.Fatalf("incorrect VKey filename: %s", raw)
		}
		return &http.Response{StatusCode: 200, Body: io.NopCloser(strings.NewReader(`{"req_1":{"code":0,"data":{"sip":["https://ws.stream.qqmusic.qq.com/"],"midurlinfo":[{"filename":"F000songmid101songmid101.flac","purl":"fixture.flac?vkey=synthetic"}]}}}`)), Header: make(http.Header)}, nil
	})}
	value, err := resolveQQ(request{Provider: "qq", Cookie: "uin=o123; qm_keyst=synthetic", Level: "lossless", Song: model.Song{ID: "songmid101"}})
	if err != nil {
		t.Fatal(err)
	}
	if value.(map[string]any)["level"] != "lossless" {
		t.Fatal("quality changed")
	}
}

func TestAccountSubjectCookies(t *testing.T) {
	for _, value := range []struct{ provider, cookie, want string }{
		{"qq", "uin=o00123; qm_keyst=fixture", "123"},
		{"qq", "wxuin=456; qm_keyst=fixture", "456"},
		{"kugou", "userid=789; token=fixture", "789"},
		{"qq", "uin=bad-subject", ""},
	} {
		if got := userID(request{Provider: value.provider, Cookie: value.cookie}); got != value.want {
			t.Fatalf("subject: got %q want %q", got, value.want)
		}
	}
}

func TestQQSearchRetainsPaidMetadataWithoutRequiringMembership(t *testing.T) {
	previous := http.DefaultClient
	defer func() { http.DefaultClient = previous }()
	count := 0
	http.DefaultClient = &http.Client{Transport: roundTripFunc(func(r *http.Request) (*http.Response, error) {
		count++
		return &http.Response{StatusCode: 200, Header: make(http.Header), Body: io.NopCloser(strings.NewReader(`{"code":0,"data":{"song":{"list":[{"songid":101,"songmid":"paid101","songname":"Fixture","albumname":"Album","interval":180,"singer":[{"name":"Artist"}],"pay":{"payplay":1,"paydownload":1}}]}}}`))}, nil
	})}
	value, err := searchQQ(request{Provider: "qq", Query: "Fixture Artist"})
	if err != nil {
		t.Fatal(err)
	}
	songs := value.([]model.Song)
	if count != 1 || len(songs) != 1 || songs[0].ID != "paid101" || songs[0].Album != "Album" {
		t.Fatal("paid metadata was hidden")
	}
}
