package store

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"time"
)

// SupabaseClient is a minimal HTTP client for the Supabase REST API.
type SupabaseClient struct {
	baseURL string
	anonKey string
	http    *http.Client
}

// NewSupabaseClient creates a SupabaseClient targeting the given project URL.
func NewSupabaseClient(baseURL, anonKey string) *SupabaseClient {
	return &SupabaseClient{
		baseURL: baseURL,
		anonKey: anonKey,
		http:    &http.Client{Timeout: 10 * time.Second},
	}
}

// ResponsePair is the data model for a captured prod/shadow response pair.
type ResponsePair struct {
	DeploymentID  string            `json:"deployment_id"`
	RequestPath   string            `json:"request_path"`
	RequestMethod string            `json:"request_method"`
	ProdStatus    int               `json:"prod_status"`
	ProdHeaders   map[string]string `json:"prod_headers"`
	ProdBody      string            `json:"prod_body"`
	ShadowStatus  int               `json:"shadow_status"`
	ShadowHeaders map[string]string `json:"shadow_headers"`
	ShadowBody    string            `json:"shadow_body"`
}

// insertResponse is used to parse the row ID returned by Supabase on insert.
type insertResponse []struct {
	ID string `json:"id"`
}

// StoreResponsePair inserts a response pair into the response_pairs table and
// returns the newly created row ID.
func (s *SupabaseClient) StoreResponsePair(pair ResponsePair) (string, error) {
	data, err := json.Marshal(pair)
	if err != nil {
		return "", fmt.Errorf("marshalling response pair: %w", err)
	}

	url := s.baseURL + "/rest/v1/response_pairs"
	req, err := http.NewRequest(http.MethodPost, url, bytes.NewBuffer(data))
	if err != nil {
		return "", fmt.Errorf("building request: %w", err)
	}
	s.setHeaders(req)
	req.Header.Set("Prefer", "return=representation")

	resp, err := s.http.Do(req)
	if err != nil {
		return "", fmt.Errorf("posting to supabase: %w", err)
	}
	defer resp.Body.Close()

	body, _ := io.ReadAll(resp.Body)
	if resp.StatusCode >= 300 {
		return "", fmt.Errorf("supabase returned %d: %s", resp.StatusCode, string(body))
	}

	var rows insertResponse
	if err := json.Unmarshal(body, &rows); err != nil {
		return "", fmt.Errorf("parsing supabase response: %w", err)
	}
	if len(rows) == 0 {
		return "", fmt.Errorf("supabase returned empty rows")
	}
	return rows[0].ID, nil
}

// setHeaders applies the required Supabase API headers to a request.
func (s *SupabaseClient) setHeaders(req *http.Request) {
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("apikey", s.anonKey)
	req.Header.Set("Authorization", "Bearer "+s.anonKey)
}
