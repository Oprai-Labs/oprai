package jobs

import (
	"context"
	"log/slog"
	"time"

	"github.com/oprai/oprai/services/admin-service-go/internal/db"
)

// Runner computes and persists daily aggregate statistics into
// admin_schema.daily_stats on an hourly schedule. The UPSERT is idempotent,
// so running multiple times per day simply refreshes the row with up-to-date
// counts. The initial run covers yesterday (to backfill) and today.
type Runner struct {
	queries *db.Queries
}

// NewRunner creates a new daily stats Runner.
func NewRunner(queries *db.Queries) *Runner {
	return &Runner{queries: queries}
}

// Start launches the background goroutine. The goroutine exits when ctx is cancelled.
func (r *Runner) Start(ctx context.Context) {
	go r.run(ctx)
}

func (r *Runner) run(ctx context.Context) {
	// Populate immediately on startup (catch-up in case service was down).
	r.computeAndStore(ctx, yesterday())
	r.computeAndStore(ctx, today())

	ticker := time.NewTicker(1 * time.Hour)
	defer ticker.Stop()

	for {
		select {
		case <-ctx.Done():
			return
		case t := <-ticker.C:
			r.computeAndStore(ctx, t.UTC().Truncate(24*time.Hour))
			// Refresh IP wallet summary once per day at 02:xx UTC.
			if t.UTC().Hour() == 2 {
				r.refreshIPSummary(ctx)
			}
		}
	}
}

func (r *Runner) computeAndStore(ctx context.Context, date time.Time) {
	stats, err := r.queries.ComputeDailyStats(ctx, date)
	if err != nil {
		slog.Error("daily_stats: failed to compute stats", "date", date.Format("2006-01-02"), "error", err)
		return
	}
	if err := r.queries.UpsertDailyStats(ctx, stats); err != nil {
		slog.Error("daily_stats: failed to upsert stats", "date", date.Format("2006-01-02"), "error", err)
		return
	}
	slog.Info("daily_stats: computed and stored", "date", date.Format("2006-01-02"))
}

func (r *Runner) refreshIPSummary(ctx context.Context) {
	if err := r.queries.RefreshIPWalletSummary(ctx); err != nil {
		slog.Error("daily_stats: failed to refresh IP wallet summary", "error", err)
		return
	}
	slog.Info("daily_stats: IP wallet summary refreshed")
}

func today() time.Time     { return time.Now().UTC().Truncate(24 * time.Hour) }
func yesterday() time.Time { return today().AddDate(0, 0, -1) }
