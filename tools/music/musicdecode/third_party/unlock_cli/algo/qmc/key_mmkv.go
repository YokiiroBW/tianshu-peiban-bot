// SPDX-License-Identifier: MIT
// TianShu adaptation: external MMKV vaults are unavailable in the offline tool.
// Self-contained QMC keys continue to use the original upstream algorithms.
package qmc
import (
    "errors"
    "go.uber.org/zap"
)
var errExternalKey = errors.New("external MMKV key required")
func readKeyFromMMKV(file string, logger *zap.Logger) ([]byte, error) { return nil, errExternalKey }
func readKeyFromMMKVCustom(mid string) ([]byte, error) { return nil, errExternalKey }
func OpenMMKV(path, key string, logger *zap.Logger) error { return errExternalKey }
