// SPDX-License-Identifier: AGPL-3.0-or-later
package main

import (
	"encoding/json"
	"errors"
	"net/http"
	"net/url"
	"strconv"
	"strings"

	"github.com/guohuiyuan/music-lib/model"
)

// Metadata must include paid songs: entitlement is checked per acquisition, not per search.
func searchQQ(r request) (any, error) {
	endpoint := "https://c.y.qq.com/soso/fcgi-bin/search_for_qq_cp?format=json&p=1&n=30&w=" + url.QueryEscape(r.Query)
	req, err := http.NewRequest(http.MethodGet, endpoint, nil)
	if err != nil {
		return nil, err
	}
	req.Header.Set("User-Agent", "Mozilla/5.0")
	req.Header.Set("Referer", "https://y.qq.com/")
	req.Header.Set("Cookie", r.Cookie)
	response, err := http.DefaultClient.Do(req)
	if err != nil {
		return nil, err
	}
	defer response.Body.Close()
	if response.StatusCode != 200 {
		return nil, errors.New("search upstream unavailable")
	}
	var value struct {
		Code int `json:"code"`
		Data struct {
			Song struct {
				List []struct {
					ID       int64  `json:"songid"`
					MID      string `json:"songmid"`
					Name     string `json:"songname"`
					Album    string `json:"albumname"`
					AlbumMID string `json:"albummid"`
					Interval int    `json:"interval"`
					Singer   []struct {
						Name string `json:"name"`
					} `json:"singer"`
				} `json:"list"`
			} `json:"song"`
		} `json:"data"`
	}
	if err := json.NewDecoder(response.Body).Decode(&value); err != nil {
		return nil, err
	}
	if value.Code != 0 {
		return nil, errors.New("search upstream unavailable")
	}
	songs := make([]model.Song, 0, len(value.Data.Song.List))
	for _, row := range value.Data.Song.List {
		if row.MID == "" {
			continue
		}
		artists := make([]string, 0, len(row.Singer))
		for _, artist := range row.Singer {
			artists = append(artists, artist.Name)
		}
		cover := ""
		if row.AlbumMID != "" {
			cover = "https://y.gtimg.cn/music/photo_new/T002R300x300M000" + row.AlbumMID + ".jpg"
		}
		songs = append(songs, model.Song{Source: "qq", ID: row.MID, Name: row.Name, Artist: strings.Join(artists, "、"), Album: row.Album,
			AlbumID: row.AlbumMID, Duration: row.Interval, Cover: cover, Extra: map[string]string{"songmid": row.MID, "song_id": strconv.FormatInt(row.ID, 10)}})
	}
	return songs, nil
}
