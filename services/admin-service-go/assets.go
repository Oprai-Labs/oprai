package adminassets

import _ "embed"

// AnalyticsViewsSQL holds the FAZ-2 analytics view definitions, embedded into
// the binary so admin-service can (re)apply them on boot without the .sql file
// needing to exist in the runtime image. Source of truth stays
// sql/analytics_views.sql. Applied as admin_app (the role admin-service connects
// as); the cross-schema GRANTs it depends on come from the chat migration
// 20260817_grant_analytics_to_admin.
//
//go:embed sql/analytics_views.sql
var AnalyticsViewsSQL string
