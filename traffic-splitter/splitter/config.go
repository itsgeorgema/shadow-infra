package splitter

import (
	"fmt"
	"os"
	"strconv"
)

// Config holds all runtime configuration for the traffic splitter.
type Config struct {
	ProdURL          string
	ShadowURL        string
	ShadowSampleRate float64
	ComparisonAPIURL string
	SupabaseURL      string
	SupabaseAnonKey  string
	DeploymentID     string
	ListenAddr       string
}

// LoadConfig reads configuration from environment variables, applying defaults where appropriate.
func LoadConfig() (*Config, error) {
	cfg := &Config{
		ProdURL:          os.Getenv("PROD_URL"),
		ShadowURL:        os.Getenv("SHADOW_URL"),
		ComparisonAPIURL: os.Getenv("COMPARISON_API_URL"),
		SupabaseURL:      os.Getenv("SUPABASE_URL"),
		SupabaseAnonKey:  os.Getenv("SUPABASE_ANON_KEY"),
		DeploymentID:     os.Getenv("DEPLOYMENT_ID"),
		ListenAddr:       ":8080",
	}

	if cfg.ProdURL == "" {
		return nil, fmt.Errorf("PROD_URL is required")
	}
	// SHADOW_URL is optional — if empty, shadowing is disabled until pr-watcher
	// patches the Deployment with an active shadow target.
	if cfg.ComparisonAPIURL == "" {
		return nil, fmt.Errorf("COMPARISON_API_URL is required")
	}

	rateStr := os.Getenv("SHADOW_SAMPLE_RATE")
	if rateStr == "" {
		cfg.ShadowSampleRate = 0.01
	} else {
		rate, err := strconv.ParseFloat(rateStr, 64)
		if err != nil {
			return nil, fmt.Errorf("invalid SHADOW_SAMPLE_RATE %q: %w", rateStr, err)
		}
		if rate < 0 || rate > 1 {
			return nil, fmt.Errorf("SHADOW_SAMPLE_RATE must be between 0 and 1, got %f", rate)
		}
		cfg.ShadowSampleRate = rate
	}

	if addr := os.Getenv("LISTEN_ADDR"); addr != "" {
		cfg.ListenAddr = addr
	}

	return cfg, nil
}
