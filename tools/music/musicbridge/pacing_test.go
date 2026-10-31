package main

import (
	"context"
	"io"
	"net/http"
	"strings"
	"testing"
	"time"
)

func TestRateLimitStopsNestedCallsAndPreservesRetryAfter(t *testing.T) {
	calls := 0
	pacing := &requestPacing{limit: 32}
	transport := officialTransport{pacing: pacing, next: roundTripFunc(func(r *http.Request) (*http.Response, error) {
		calls++
		return &http.Response{StatusCode: 429, Header: http.Header{"Retry-After": []string{"7200"}}, Body: io.NopCloser(strings.NewReader("limited"))}, nil
	})}
	for i := 0; i < 2; i++ {
		req, _ := http.NewRequest(http.MethodGet, "https://y.qq.com/test", nil)
		_, err := transport.RoundTrip(req)
		if err == nil || errorCode(err) != "music_rate_limited" {
			t.Fatal("rate limit must reach the parent governor")
		}
	}
	if calls != 1 || pacing.used != 1 || pacing.retryAfter != 7200 {
		t.Fatalf("incorrect accounting: %d %#v", calls, pacing)
	}
}

func TestPacingBudgetAndCancelledWaitDoNotSendAnotherRequest(t *testing.T) {
	pacing := &requestPacing{limit: 1}
	req, _ := http.NewRequest(http.MethodGet, "https://y.qq.com/test", nil)
	if err := pacing.before(req); err != nil {
		t.Fatal(err)
	}
	if err := pacing.before(req); err == nil || pacing.used != 1 {
		t.Fatal("budget was exceeded")
	}
	pacing = &requestPacing{limit: 2, next: time.Now().Add(time.Hour)}
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	if err := pacing.before(req.WithContext(ctx)); err == nil || pacing.used != 0 {
		t.Fatal("cancelled wait sent a request")
	}
}
