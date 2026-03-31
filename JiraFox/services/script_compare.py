OLD_JQL = """
(
  project in (
    BUSCTCAFF,
    BUSASSUCI,
    BUSREVVIG,
    "BUS Knowledge Management",
    "BKL1 - Popu / Assu",
    "BUS Gestion des Non En Règle",
    "BUS Révision des lettres",
    BUSDROITTI,
    "Solidaris Wallonie 2.0"
  )
  AND issuetype in (Bug, "DEV Support Request", Defect, Task, Story)
  AND (
    assignee in (
      tdptd2, tdptbw, tdptvs, tdptde, tdptbc, TDPTSH, TDATNE,
      t45tjd, t45stm, tdptdp, t45p04, T45TED, t460vl, tgajod,
      T45KOR, T60PDM
    )
    OR reporter in (
      tdptbc, tdatne, tdptd2, tdptde
    )
    OR creator in (
      tdptd2, tdptbw, tdptvs, tdptde, tdptbc, TDPTSH, TDATNE,
      t45tjd, t45stm, T45TED, tdptdp, t45p04, tgattr,
      t460vl, T45KOR, T60PDM, TDPTAV
    )
  )
  AND (
    project != BUSREVLET
    OR "Epic Link" = BUSREVLET-2
    OR labels = BUSREVLET_ASSU_POP
  )
  AND (
    project != BUSSW20
    OR "Epic Link" = BUSSW20-7
  )
  AND (
    project != BUSKM
    OR "Epic Link" in (BUSKM-234, BUSKM-235, BUSKM-236)
  )
)
OR
(
  project = BKL1
  AND status in (
    "10021", "11204", "10006", "11200", "10009", "10002",
    "10008", "10100", "10001", "11201", "10005", "11205",
    "10000", "11202", "10003", "11206", "3", "11203"
  )
  AND issuetype != "10000"
  AND status not in ("10009", "10002", "10006")
  AND (
    cf[10104] is EMPTY
    OR (
      cf[10104] not in futureSprints()
      AND cf[10104] not in openSprints()
    )
  )
)
OR
(
  project = BUSDIKINE
  AND (
    creator in (TDPTBC, TDPTAV, TDATNE, T45TJD, T45STM)
    OR reporter in (TDPTBC, TDPTAV, TDATNE, T45TJD, T45STM)
    OR assignee in (TDPTBC, TDPTAV, TDATNE, T45TJD, T45STM)
  )
)
OR
(
  labels = "ASSU_POPU_SPRINT"
)
ORDER BY Rank ASC
"""

NEW_JQL = """(
  project in (BKL1, BUSCTCAFF, BUSASSUCI, BUSREVVIG, BUSGESTNER, BUSDROITTI)
  AND issuetype in standardIssueTypes()
  AND (
    assignee in (
     tdptd2, tdptbw, tdptvs, tdptde, tdptbc, TDPTSH, TDATNE,
      t45tjd, t45stm, T45TED, tdptdp, t45p04, tgattr,
      t460vl, T45KOR, T60PDM, TDPTAV, tgattr
    )
    OR reporter in (
     tdptd2, tdptbw, tdptvs, tdptde, tdptbc, TDPTSH, TDATNE,
      t45tjd, t45stm, T45TED, tdptdp, t45p04, tgattr,
      t460vl, T45KOR, T60PDM, TDPTAV
    )
    OR creator in (
      tdptd2, tdptbw, tdptvs, tdptde, tdptbc, TDPTSH, TDATNE,
      t45tjd, t45stm, T45TED, tdptdp, t45p04, tgattr,
      t460vl, T45KOR, T60PDM, TDPTAV
    )
  )
)
OR project = BUSREVLET AND "Epic Link" = BUSREVLET-2
OR project = BUSSW20 AND "Epic Link" = BUSSW20-7
OR project = BUSKM AND "Epic Link" in (BUSKM-234, BUSKM-235, BUSKM-236)
OR labels = "ASSU_POPU_SPRINT"
ORDER BY Rank ASC"""


def get_query_diff(jira) -> list[tuple[str, str]]:
    original_issues = jira.run_jql(OLD_JQL, full=False)
    refactored_issues = jira.run_jql(NEW_JQL, full=False)
    refactored_keys = {issue["key"] for issue in refactored_issues}
    return [
        (issue["key"], issue["summary"])
        for issue in original_issues
        if issue["key"] not in refactored_keys
    ]
