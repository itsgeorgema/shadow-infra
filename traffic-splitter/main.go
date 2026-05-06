package main

import (
	"log"
	"net/http"

	"github.com/itsgeorgema/shadow-infra/traffic-splitter/splitter"
)

func main() {
	cfg, err := splitter.LoadConfig()
	if err != nil {
		log.Fatalf("configuration error: %v", err)
	}

	proxy, err := splitter.NewTeeProxy(cfg)
	if err != nil {
		log.Fatalf("failed to build proxy: %v", err)
	}

	mux := http.NewServeMux()

	// Health check — always returns 200.
	mux.HandleFunc("/health", func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusOK)
		w.Write([]byte(`{"status":"ok"}`)) //nolint:errcheck
	})

	// All other traffic is handled by the tee proxy.
	mux.Handle("/", proxy)

	log.Printf("traffic-splitter listening on %s (prod=%s, shadow=%s, rate=%.4f)",
		cfg.ListenAddr, cfg.ProdURL, cfg.ShadowURL, cfg.ShadowSampleRate)

	if err := http.ListenAndServe(cfg.ListenAddr, mux); err != nil {
		log.Fatalf("server error: %v", err)
	}
}
