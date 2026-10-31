package pc_kugou_db

// ported from lib_um_crypto_rust:
//   https://git.unlock-music.dev/um/lib_um_crypto_rust/src/tag/v0.1.10/um_crypto/kgm/src/pc_db_decrypt

import (
	"bytes"
	"context"
	"crypto/aes"
	"crypto/cipher"
	"crypto/md5"
	"database/sql"
	"encoding/binary"
	"fmt"
	"os"
	"sync"

	_ "modernc.org/sqlite"
)

const PAGE_SIZE = 0x400

var SQLITE_HEADER = []byte("SQLite format 3\x00")
var DEFAULT_MASTER_KEY = []byte{
	// master key (0x10 bytes)
	0x1D, 0x61, 0x31, 0x45, 0xB2, 0x47, 0xBF, 0x7F, 0x3D, 0x18, 0x96, 0x72, 0x14, 0x4F, 0xE4, 0xBF,
	0x00, 0x00, 0x00, 0x00, // page number (le)
	0x73, 0x41, 0x6C, 0x54, // fixed value
}

func next_page_iv(seed uint32) uint32 {
	var left uint32 = seed * 0x9EF4
	var right uint32 = seed / 0xce26 * 0x7FFFFF07
	var value uint32 = left - right
	if value&0x8000_0000 == 0 {
		return value
	}
	return value + 0x7FFF_FF07
}

func derive_page_aes_key(seed uint32) []byte {
	master_key := make([]byte, len(DEFAULT_MASTER_KEY))
	copy(master_key, DEFAULT_MASTER_KEY)
	binary.LittleEndian.PutUint32(master_key[0x10:0x14], seed)
	digest := md5.Sum(master_key)
	return digest[:]
}

func derive_page_aes_iv(seed uint32) []byte {
	iv := make([]byte, 0x10)
	seed = seed + 1
	for i := 0; i < 0x10; i += 4 {
		seed = next_page_iv(seed)
		binary.LittleEndian.PutUint32(iv[i:i+4], seed)
	}
	digest := md5.Sum(iv)
	return digest[:]
}

func aes128cbcDecryptNoPadding(buffer, key, iv []byte) error {
	if len(key) != 16 {
		return fmt.Errorf("invalid key size: %d (must be 16 bytes for AES-128)", len(key))
	}
	if len(iv) != aes.BlockSize {
		return fmt.Errorf("invalid IV size: %d (must be %d bytes)", len(iv), aes.BlockSize)
	}
	if len(buffer)%aes.BlockSize != 0 {
		return fmt.Errorf("ciphertext length must be a multiple of %d bytes", aes.BlockSize)
	}

	block, err := aes.NewCipher(key)
	if err != nil {
		return err
	}

	mode := cipher.NewCBCDecrypter(block, iv)
	mode.CryptBlocks(buffer, buffer)
	return nil
}

func decrypt_db_page(buffer []byte, page_number uint32) error {
	key := derive_page_aes_key(page_number)
	iv := derive_page_aes_iv(page_number)

	return aes128cbcDecryptNoPadding(buffer, key, iv)
}

func decrypt_page_1(page []byte) error {
	if err := validate_page_1_header(page); err != nil {
		return err
	}

	// Backup expected hdr value

	expected_hdr_value := make([]byte, 8)
	copy(expected_hdr_value, page[0x10:0x18])

	// Copy encrypted hdr over
	hdr := page[:0x10]
	copy(page[0x10:0x18], hdr[0x08:0x10])

	if err := decrypt_db_page(page[0x10:], 1); err != nil {
		return err
	}

	// Validate header
	if !bytes.Equal(page[0x10:0x18], expected_hdr_value[:8]) {
		return fmt.Errorf("decrypt page 1 failed")
	}

	// Apply SQLite header
	copy(hdr, SQLITE_HEADER)

	return nil
}

func validate_page_1_header(header []byte) error {
	o10 := binary.LittleEndian.Uint32(header[0x10:0x14])
	o14 := binary.LittleEndian.Uint32(header[0x14:0x18])

	v6 := ((o10 & 0xff) << 8) | ((o10 & 0xff00) << 16)
	ok := o14 == 0x20204000 && (v6-0x200) <= 0xFE00 && ((v6-1)&v6) == 0
	if !ok {
		return fmt.Errorf("invalid page 1 header")
	}
	return nil
}

func decryptPcDatabase(buffer []byte) error {
	db_size := len(buffer)

	// not encrypted
	if bytes.Equal(buffer[:len(SQLITE_HEADER)], SQLITE_HEADER) {
		return nil
	}

	if db_size%PAGE_SIZE != 0 || db_size == 0 {
		return fmt.Errorf("invalid database size: %d", db_size)
	}

	last_page := db_size / PAGE_SIZE

	// page 1 is the header
	if err := decrypt_page_1(buffer[:PAGE_SIZE]); err != nil {
		return err
	}

	offset := PAGE_SIZE
	for page_no := 2; page_no <= last_page; page_no++ {
		if err := decrypt_db_page(buffer[offset:offset+PAGE_SIZE], uint32(page_no)); err != nil {
			return err
		}
		offset += PAGE_SIZE
	}

	return nil
}

func extractKeyMapping(buffer []byte) (map[string]string, error) {
	// Create an in-memory SQLite database
	db, err := sql.Open("sqlite", "file::memory:?mode=memory&cache=shared")
	if err != nil {
		return nil, err
	}
	defer db.Close()

	conn, err := db.Conn(context.Background())
	if err != nil {
		return nil, err
	}

	err = func() error {
		defer conn.Close()
		return conn.Raw(func(driverConn any) error {
			type serializer interface {
				Serialize() ([]byte, error)
				Deserialize([]byte) error
			}
			return driverConn.(serializer).Deserialize(buffer)
		})
	}()

	if err != nil {
		return nil, fmt.Errorf("failed to import db: %w", err)
	}

	conn, err = db.Conn(context.Background())
	if err != nil {
		return nil, err
	}

	rows, err := conn.QueryContext(context.Background(), `
		select EncryptionKeyId, EncryptionKey from ShareFileItems
		where EncryptionKey != '' and EncryptionKey is not null
	`)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	m := make(map[string]string)
	for rows.Next() {
		var keyId, key string
		if err := rows.Scan(&keyId, &key); err != nil {
			continue
		}
		m[keyId] = key
	}

	return m, err
}

var kugouPcDatabaseDumpLock = &sync.Mutex{}
var kugouPcDatabaseDump = make(map[string]map[string]string)

func CachedDumpEKey(dbPath string) (map[string]string, error) {
	dump, exist := kugouPcDatabaseDump[dbPath]
	if !exist {
		kugouPcDatabaseDumpLock.Lock()
		defer kugouPcDatabaseDumpLock.Unlock()

		if dump, exist = kugouPcDatabaseDump[dbPath]; !exist {
			buffer, err := os.ReadFile(dbPath)
			if err != nil {
				return nil, err
			}
			if err = decryptPcDatabase(buffer); err != nil {
				return nil, err
			}
			dump, err = extractKeyMapping(buffer)
			if err != nil {
				return nil, err
			}
			kugouPcDatabaseDump[dbPath] = dump
		}
	}

	return dump, nil
}
