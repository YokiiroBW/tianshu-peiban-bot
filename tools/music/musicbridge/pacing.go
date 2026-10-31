// SPDX-License-Identifier: AGPL-3.0-or-later
package main

import (
	"errors"
	"math"
	"net/http"
	"strconv"
	"sync"
	"time"
)

// One invocation owns a reservation from the Platform's persistent account budget.
// All library clients use the same transport, including pagination and redirects.
type requestPacing struct {
	mu          sync.Mutex
	interval    time.Duration
	limit, used int
	next        time.Time
	stopped     error
	retryAfter  float64
}

func (p *requestPacing) before(r *http.Request) error {
	p.mu.Lock()
	defer p.mu.Unlock()
	if p.stopped != nil {
		return p.stopped
	}
	if p.used >= p.limit {
		return errors.New("music request budget exhausted")
	}
	if wait := time.Until(p.next); wait > 0 {
		timer := time.NewTimer(wait)
		defer timer.Stop()
		select {
		case <-timer.C:
		case <-r.Context().Done():
			return r.Context().Err()
		}
	}
	p.used++
	p.next = time.Now().Add(p.interval)
	return nil
}

func (p *requestPacing) observe(response *http.Response) error {
	p.mu.Lock()
	defer p.mu.Unlock()
	if response.StatusCode == 429 {
		p.stopped = errors.New("music rate limited")
		if seconds, err := strconv.ParseFloat(response.Header.Get("Retry-After"), 64); err == nil && seconds > 0 && !math.IsInf(seconds, 0) && !math.IsNaN(seconds) {
			p.retryAfter = seconds
		} else if date, err := http.ParseTime(response.Header.Get("Retry-After")); err == nil {
			p.retryAfter = time.Until(date).Seconds()
		}
	} else if response.StatusCode == 401 {
		p.stopped = errors.New("authentication required")
	}
	return p.stopped
}
