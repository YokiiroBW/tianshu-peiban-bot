package main

import (
	"bytes"
	"encoding/binary"
	"errors"
	"os"
	"path/filepath"
	"testing"

	"unlock-music.dev/cli/algo/qmc"
)

func TestQMCStaticDecoderPreservesBytes(t *testing.T) {
	original := append([]byte("fLaC"), bytes.Repeat([]byte{1, 2, 3, 4}, 1024)...)
	encrypted := append([]byte(nil), original...)
	cipher, err := qmc.NewQmcCipherDecoder(nil)
	if err != nil {
		t.Fatal(err)
	}
	cipher.Decrypt(encrypted, 0)
	directory := t.TempDir()
	source, output := filepath.Join(directory, "synthetic.qmcflac"), filepath.Join(directory, "decoded")
	if err := os.WriteFile(source, encrypted, 0600); err != nil {
		t.Fatal(err)
	}
	if _, err := decode(request{Input: source, Output: output}); err != nil {
		t.Fatal(err)
	}
	got, err := os.ReadFile(output)
	if err != nil || !bytes.Equal(got, original) {
		t.Fatal("decoded stream differs")
	}
	got, _ = os.ReadFile(source)
	if !bytes.Equal(got, encrypted) {
		t.Fatal("original was modified")
	}
}

func TestMissingKeysAndUnsupportedVersionDoNotCreateOutput(t *testing.T) {
	kgm := make([]byte, 90)
	binary.LittleEndian.PutUint32(kgm[20:24], 5)
	unknown := append([]byte(nil), kgm...)
	binary.LittleEndian.PutUint32(unknown[20:24], 7)
	for _, test := range []struct {
		name     string
		data     []byte
		expected error
	}{
		{"stag.mflac", append(make([]byte, 80), []byte("STag")...), errKeyRequired},
		{"vault.mflac", append(make([]byte, 80), []byte("musicex\x00")...), errKeyRequired},
		{"client.kgm", kgm, errKeyRequired},
		{"future.kgm", unknown, errUnsupported},
	} {
		t.Run(test.name, func(t *testing.T) {
			directory := t.TempDir()
			source, output := filepath.Join(directory, test.name), filepath.Join(directory, "output")
			if err := os.WriteFile(source, test.data, 0600); err != nil {
				t.Fatal(err)
			}
			_, err := decode(request{Input: source, Output: output})
			if !errors.Is(err, test.expected) {
				t.Fatalf("unexpected classification: %v", err)
			}
			if _, err := os.Stat(output); !os.IsNotExist(err) {
				t.Fatal("failed input created output")
			}
		})
	}
}
