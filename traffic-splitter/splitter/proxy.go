package splitter

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"log"
	"math/rand"
	"net/http"
	"net/http/httptest"
	"net/http/httputil"
	"net/url"
	"time"

	"github.com/itsgeorgema/shadow-infra/traffic-splitter/store"
)

// capturedResponse holds a recorded HTTP response for comparison.
type capturedResponse struct {
	Status  int               `json:"status"`
	Headers map[string]string `json:"headers"`
	Body    string            `json:"body"`
}

// comparePayload is the JSON body sent to the comparison-agent.
type comparePayload struct {
	DeploymentID   string           `json:"deployment_id"`
	PairID         string           `json:"pair_id"`
	ProdResponse   capturedResponse `json:"prod_response"`
	ShadowResponse capturedResponse `json:"shadow_response"`
}

// TeeProxy is an HTTP handler that forwards every request to the production
// upstream and probabilistically mirrors a copy to the shadow upstream.
type TeeProxy struct {
	cfg        *Config
	prodProxy  *httputil.ReverseProxy
	supaClient *store.SupabaseClient
	rng        *rand.Rand
}

// NewTeeProxy constructs a TeeProxy from the given Config.
func NewTeeProxy(cfg *Config) (*TeeProxy, error) {
	prodURL, err := url.Parse(cfg.ProdURL)
	if err != nil {
		return nil, fmt.Errorf("invalid PROD_URL: %w", err)
	}

	prodProxy := httputil.NewSingleHostReverseProxy(prodURL)
	// Rewrite the Host header so the upstream sees its own hostname.
	origDirector := prodProxy.Director
	prodProxy.Director = func(req *http.Request) {
		origDirector(req)
		req.Host = prodURL.Host
	}

	var supaClient *store.SupabaseClient
	if cfg.SupabaseURL != "" && cfg.SupabaseAnonKey != "" {
		supaClient = store.NewSupabaseClient(cfg.SupabaseURL, cfg.SupabaseAnonKey)
	}

	//nolint:gosec // non-cryptographic sampling is intentional
	rng := rand.New(rand.NewSource(time.Now().UnixNano()))

	return &TeeProxy{
		cfg:        cfg,
		prodProxy:  prodProxy,
		supaClient: supaClient,
		rng:        rng,
	}, nil
}

// ServeHTTP satisfies http.Handler.
// It reads the request body once, forwards to prod (capturing the response),
// and — if sampled — fires an async shadow request.
func (tp *TeeProxy) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	// Read request body so we can replay it for both prod and shadow.
	var reqBodyBytes []byte
	if r.Body != nil {
		var err error
		reqBodyBytes, err = io.ReadAll(r.Body)
		if err != nil {
			http.Error(w, "failed to read request body", http.StatusBadGateway)
			return
		}
		r.Body.Close()
	}

	// Restore body for the prod proxy.
	r.Body = io.NopCloser(bytes.NewBuffer(reqBodyBytes))

	// Capture the prod response via a ResponseRecorder.
	rec := httptest.NewRecorder()
	tp.prodProxy.ServeHTTP(rec, r)

	// Copy the recorded prod response to the actual ResponseWriter.
	prodResult := rec.Result()
	for k, vals := range prodResult.Header {
		for _, v := range vals {
			w.Header().Add(k, v)
		}
	}
	w.WriteHeader(prodResult.StatusCode)
	prodBodyBytes, _ := io.ReadAll(prodResult.Body)
	prodResult.Body.Close()
	w.Write(prodBodyBytes) //nolint:errcheck

	// Decide whether to shadow this request.
	if tp.rng.Float64() >= tp.cfg.ShadowSampleRate {
		return
	}

	// Clone relevant request fields for use in the goroutine.
	method := r.Method
	path := r.URL.RequestURI()
	reqBodyCopy := make([]byte, len(reqBodyBytes))
	copy(reqBodyCopy, reqBodyBytes)
	prodHeaders := flattenHeaders(prodResult.Header)
	prodBody := string(prodBodyBytes)
	prodStatus := prodResult.StatusCode
	deploymentID := tp.cfg.DeploymentID

	go func() {
		shadowResp, err := tp.fireShadowRequest(method, path, r.Header, reqBodyCopy)
		if err != nil {
			log.Printf("[shadow] request failed: %v", err)
			return
		}
		defer shadowResp.Body.Close()

		shadowBodyBytes, _ := io.ReadAll(shadowResp.Body)
		shadowHeaders := flattenHeaders(shadowResp.Header)

		prodCaptured := capturedResponse{
			Status:  prodStatus,
			Headers: prodHeaders,
			Body:    prodBody,
		}
		shadowCaptured := capturedResponse{
			Status:  shadowResp.StatusCode,
			Headers: shadowHeaders,
			Body:    string(shadowBodyBytes),
		}

		// Persist the response pair to Supabase (if configured) and get a pair ID.
		pairID := ""
		if tp.supaClient != nil && deploymentID != "" {
			pairID, err = tp.supaClient.StoreResponsePair(store.ResponsePair{
				DeploymentID:  deploymentID,
				RequestPath:   path,
				RequestMethod: method,
				ProdStatus:    prodCaptured.Status,
				ProdHeaders:   prodCaptured.Headers,
				ProdBody:      prodCaptured.Body,
				ShadowStatus:  shadowCaptured.Status,
				ShadowHeaders: shadowCaptured.Headers,
				ShadowBody:    shadowCaptured.Body,
			})
			if err != nil {
				log.Printf("[shadow] failed to store response pair: %v", err)
			}
		}

		// Send to comparison agent.
		if tp.cfg.ComparisonAPIURL != "" {
			payload := comparePayload{
				DeploymentID:   deploymentID,
				PairID:         pairID,
				ProdResponse:   prodCaptured,
				ShadowResponse: shadowCaptured,
			}
			if err := tp.postToComparisonAgent(payload); err != nil {
				log.Printf("[shadow] comparison agent error: %v", err)
			}
		}
	}()
}

// fireShadowRequest sends a mirrored request to the shadow URL.
func (tp *TeeProxy) fireShadowRequest(method, path string, originalHeaders http.Header, body []byte) (*http.Response, error) {
	shadowTarget := tp.cfg.ShadowURL + path
	req, err := http.NewRequest(method, shadowTarget, bytes.NewBuffer(body))
	if err != nil {
		return nil, fmt.Errorf("building shadow request: %w", err)
	}

	// Copy safe headers from the original request.
	for k, vals := range originalHeaders {
		// Skip hop-by-hop headers.
		switch k {
		case "Connection", "Upgrade", "Transfer-Encoding", "Keep-Alive", "Proxy-Connection":
			continue
		}
		for _, v := range vals {
			req.Header.Add(k, v)
		}
	}
	req.Header.Set("X-Shadow-Request", "true")

	client := &http.Client{Timeout: 30 * time.Second}
	return client.Do(req)
}

// postToComparisonAgent POSTs the comparison payload to the agent service.
func (tp *TeeProxy) postToComparisonAgent(payload comparePayload) error {
	data, err := json.Marshal(payload)
	if err != nil {
		return fmt.Errorf("marshalling payload: %w", err)
	}

	url := tp.cfg.ComparisonAPIURL + "/compare"
	req, err := http.NewRequest(http.MethodPost, url, bytes.NewBuffer(data))
	if err != nil {
		return fmt.Errorf("building comparison request: %w", err)
	}
	req.Header.Set("Content-Type", "application/json")

	client := &http.Client{Timeout: 60 * time.Second}
	resp, err := client.Do(req)
	if err != nil {
		return fmt.Errorf("posting to comparison agent: %w", err)
	}
	defer resp.Body.Close()

	if resp.StatusCode >= 300 {
		bodyBytes, _ := io.ReadAll(resp.Body)
		return fmt.Errorf("comparison agent returned %d: %s", resp.StatusCode, string(bodyBytes))
	}
	return nil
}

// flattenHeaders converts http.Header (multi-value) to a simple map for JSON.
func flattenHeaders(h http.Header) map[string]string {
	flat := make(map[string]string, len(h))
	for k, vals := range h {
		if len(vals) > 0 {
			flat[k] = vals[0]
		}
	}
	return flat
}
