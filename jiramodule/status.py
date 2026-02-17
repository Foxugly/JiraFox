STATUS_EXCLUDED = ["Backlog",]

CANONICAL_ORDER = [
    # ====== Intake / Création ======
    "New", "Open", "Opened", "Reopened",

    # ====== Vision / Initiative / Backlog ======
    "Vision", "Initiative in Backlog", "Backlog", "Postponed",

    # ====== Analyse / Qualification ======
    "To Analyse", "Analyse", "Analyse Pending", "Analyse Pending",  # (si doublon, enlève une occurrence)
    "In Analyse", "Analysis ongoing", "Under investigation", "Qualification",
    "Awaiting BP approval",

    # ====== Ready / priorisation / préparation sprint ======
    "Waiting for priority",
    "Ready", "Ready for Business", "Ready for sprint",
    "Selected for Development", "To Development",
    "Ready for Review",          # parfois “ready” avant dev (si vous faites design review)
    "To Do",

    # ====== Exécution / Dev ======
    "Implementing", "In Progress", "Initiative in Progress",

    # ====== Blocages / dépendances / support (états transverses) ======
    "Blocked internally", "Blocking", "Blocked externally",
    "Waiting for support", "Waiting for Supplier",
    "Waiting for customer", "Waiting for feedback", "Waiting Feedback",
    "Waiting for approval", "Waiting for Validation",  "Pending",

    # ====== Review ======
    "Pull Request", "Under review", "In Review",

    # ====== Test / Validation / Acceptance ======
    "Ready for Test", "In Test",
    "Ready for Validation", "In Validation", "To validation",
    "Ready for acceptance", "In acceptance",
    "Ready to deploy Test",
    "Waiting for Go/NoGo",

    # ====== Release / Déploiement ======
    "Ready for release", "Deployed",

    # ====== Done-ish (fin de vie) ======
    "Resolved", "Completed", "Issue Completed", "Closed", "Done",

    # ====== Rejet / Annulation / Échec ======
    "Rejected", "Declined", "Canceled", "Failed",
]
