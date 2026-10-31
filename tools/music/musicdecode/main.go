// SPDX-License-Identifier: MIT
// Decode a single staged file using Unlock Music. Source files are never removed.
package main

import (
	"context"
	"encoding/binary"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"runtime/debug"
	"strings"
	"time"

	"go.uber.org/zap"
	"unlock-music.dev/cli/algo/common"
	_ "unlock-music.dev/cli/algo/kgm"
	_ "unlock-music.dev/cli/algo/ncm"
	_ "unlock-music.dev/cli/algo/qmc"
)

type request struct {
	Input     string `json:"input"`
	Output    string `json:"output"`
	Cover     string `json:"cover"`
	Operation string `json:"operation"`
}
type offline struct{}

var errKeyRequired = errors.New("client key required")
var errUnsupported = errors.New("unsupported encryption version")

func validateEnvelope(f *os.File, extension string, size int64) error {
	if extension == ".ncm" {
		return validateNCM(f, size)
	}
	if extension == ".kgm" || extension == ".kgma" || extension == ".kgg" || extension == ".vpr" {
		header := make([]byte, 60)
		if _, err := io.ReadFull(f, header); err != nil {
			return err
		}
		switch binary.LittleEndian.Uint32(header[20:24]) {
		case 5:
			return errKeyRequired // This build has no client KGG database.
		case 3:
			offset := int64(binary.LittleEndian.Uint32(header[16:20]))
			if offset < 60 || offset >= size {
				return errors.New("invalid kgm audio offset")
			}
		default:
			return errUnsupported
		}
	} else if size >= 8 {
		_, _ = f.Seek(-8, io.SeekEnd)
		tail := make([]byte, 8)
		if _, err := io.ReadFull(f, tail); err != nil {
			return err
		}
		if string(tail[4:]) == "STag" || string(tail) == "musicex\x00" {
			return errKeyRequired
		}
	}
	_, err := f.Seek(0, io.SeekStart)
	return err
}

func (offline) RoundTrip(*http.Request) (*http.Response, error) {
	return nil, errors.New("offline decoder")
}

func validateNCM(f *os.File, size int64) error {
	if _, err := f.Seek(10, io.SeekStart); err != nil {
		return err
	}
	for _, limit := range []uint32{1 << 20, 16 << 20} {
		var length uint32
		if err := binary.Read(f, binary.LittleEndian, &length); err != nil {
			return err
		}
		at, err := f.Seek(0, io.SeekCurrent)
		if err != nil || length > limit || at+int64(length) > size {
			return errors.New("invalid ncm frame")
		}
		if _, err = f.Seek(int64(length), io.SeekCurrent); err != nil {
			return err
		}
	}
	if _, err := f.Seek(5, io.SeekCurrent); err != nil {
		return err
	}
	var frame, length uint32
	if err := binary.Read(f, binary.LittleEndian, &frame); err != nil {
		return err
	}
	if err := binary.Read(f, binary.LittleEndian, &length); err != nil {
		return err
	}
	at, err := f.Seek(0, io.SeekCurrent)
	if err != nil || length > 16<<20 || frame < length || at+int64(frame) >= size {
		return errors.New("invalid ncm cover")
	}
	_, err = f.Seek(0, io.SeekStart)
	return err
}

func decode(r request) (map[string]any, error) {
	f, err := os.Open(r.Input)
	if err != nil {
		return nil, err
	}
	defer f.Close()
	stat, err := f.Stat()
	if err != nil || !stat.Mode().IsRegular() || stat.Size() > 1<<30 {
		return nil, errors.New("invalid input")
	}
	extension := strings.ToLower(filepath.Ext(r.Input))
	if err := validateEnvelope(f, extension, stat.Size()); err != nil {
		return nil, err
	}
	var dec common.Decoder
	factories := common.GetDecoder(r.Input, true)
	if len(factories) == 0 {
		return nil, errUnsupported
	}
	for _, factory := range factories {
		_, _ = f.Seek(0, io.SeekStart)
		candidate := factory.Create(&common.DecoderParams{Reader: f, Extension: extension, FilePath: r.Input, Logger: zap.NewNop()})
		if candidate.Validate() == nil {
			dec = candidate
			break
		}
	}
	if dec == nil {
		return nil, errors.New("unsupported or missing key")
	}
	output, err := os.OpenFile(r.Output, os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0600)
	if err != nil {
		return nil, err
	}
	size, err := io.Copy(output, io.LimitReader(dec, (1<<30)+1))
	if err == nil && size > 1<<30 {
		err = errors.New("oversize output")
	}
	if err == nil {
		err = output.Sync()
	}
	closeErr := output.Close()
	if err == nil {
		err = closeErr
	}
	if err != nil {
		return nil, err
	}
	result := map[string]any{"size": size, "tool": "unlock-music/cli", "version": "v0.2.12"}
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	if getter, ok := dec.(common.AudioMetaGetter); ok {
		if metadata, e := getter.GetAudioMeta(ctx); e == nil && metadata != nil {
			result["metadata"] = metadata
			result["title"] = metadata.GetTitle()
			result["album"] = metadata.GetAlbum()
			result["artists"] = metadata.GetArtists()
		}
	}
	if getter, ok := dec.(common.CoverImageGetter); ok && r.Cover != "" {
		if cover, e := getter.GetCoverImage(ctx); e == nil && len(cover) > 0 && len(cover) <= 16<<20 {
			image, e := os.OpenFile(r.Cover, os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0600)
			if e == nil {
				_, e = image.Write(cover)
				if e == nil {
					e = image.Sync()
				}
				closeErr = image.Close()
				if e == nil && closeErr == nil {
					result["cover_written"] = true
				}
			}
		}
	}
	return result, nil
}

func main() {
	debug.SetMemoryLimit(256 << 20)
	http.DefaultTransport = offline{}
	output := map[string]any{}
	defer func() {
		if recover() != nil {
			output = map[string]any{"ok": false, "code": "music_decode_failed"}
		}
		_ = json.NewEncoder(os.Stdout).Encode(output)
	}()
	var r request
	if json.NewDecoder(io.LimitReader(os.Stdin, 32<<10)).Decode(&r) != nil {
		output["ok"] = false
		output["code"] = "invalid_input"
		return
	}
	if r.Operation == "version" {
		output["ok"] = true
		output["value"] = map[string]any{"protocol": 1, "unlock_music_version": "v0.2.12"}
		return
	}
	result, err := decode(r)
	if err != nil {
		output["ok"] = false
		output["code"] = "music_decode_failed"
		if errors.Is(err, errKeyRequired) {
			output["code"] = "music_decode_key_required"
		} else if errors.Is(err, errUnsupported) {
			output["code"] = "music_decode_unsupported"
		}
		return
	}
	output["ok"] = true
	output["value"] = result
}
