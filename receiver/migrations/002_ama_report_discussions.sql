-- Current public thread for each AMA report, refreshed by report-comments.
CREATE TABLE IF NOT EXISTS ama_report_discussions (
    report_id uuid PRIMARY KEY,
    title text NOT NULL,
    created_at timestamptz NOT NULL,
    comments jsonb NOT NULL
);
