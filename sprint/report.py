from io import BytesIO
from openpyxl import Workbook
from openpyxl.styles import PatternFill, Side, Border
from openpyxl.utils import get_column_letter

from jiramodule.services.jira_client import JiraManager
from team.models import TeamDev

#FIRSTNAMES = {'Benjamin': 5, 'Wim': 1, 'Sybren': 6, 'Stéphane': 3, 'Joseph': 4, 'Nancy': 0, 'Bart': 2}


def create_xlsx_bytes(jm: JiraManager, sprint_id:int) -> bytes:
    buffer = BytesIO()
    fill_sum = PatternFill(start_color="ff6565", end_color="ff6565", fill_type="solid")
    fill_title = PatternFill(start_color="ff8f8f", end_color="ff8f8f", fill_type="solid")
    fill = PatternFill(start_color="ffb9b9", end_color="ffb9b9", fill_type="solid")
    # --- Ici, ON REUTILISE TON CODE A L’IDENTIQUE ---
    wb = Workbook()
    ws1 = wb.active
    ws1.title = "Capacity sheet"
    available_row = 7
    start_col = 3  # C
    title_col = 2  # B
    #state_col = start_col + len(FIRSTNAMES)  # colonne après les prénoms

    #ordered_FIRSTNAMES = sorted(FIRSTNAMES.items(), key=lambda x: x[1])

    team = jm.cfg.team
    devs = TeamDev.objects.filter(team=team).select_related("dev").order_by("order", "id")
    ordered_FIRSTNAMES = [d.dev.name for d in devs]
    state_col = start_col + len(ordered_FIRSTNAMES)
    list_sum_rows: list[int] = []

    def write_header(ws, row: int) -> int:
        # ----------- FIRSTNAME ---------------------
        for i in range(1, start_col):
            c = ws.cell(row=row, column=i)
            c.fill = fill_sum
        for i, name in enumerate(ordered_FIRSTNAMES):
            c = ws.cell(row=row, column=start_col + i, value=name)
            c.fill = fill_sum
        c = ws.cell(row=row, column=state_col, value="State")
        c.fill = fill_sum
        ws.freeze_panes = "A2"
        row += 1
        #------------ CAPACITY -----------------
        ws.cell(row=row, column=start_col - 1, value="Capacity")
        for i, name in enumerate(ordered_FIRSTNAMES):
            c = ws.cell(row=row, column=start_col + i, value=72)
            c.fill = fill
        row += 1
        # ------------ HOLIDAY --------------------
        ws.cell(row=row, column=start_col - 1, value="Holidays")
        for i, name in enumerate(ordered_FIRSTNAMES):
            c = ws.cell(row=row, column=start_col + i, value=0)
            c.fill = fill
        row += 1
        # ------------ FASTLANE ------------------
        ws.cell(row=row, column=start_col - 1, value="Fastlane (10%)")
        for i, name in enumerate(ordered_FIRSTNAMES):
            col = start_col + i
            col_letter = get_column_letter(col)
            value = f"=({col_letter}{row-2}-{col_letter}{row - 1})* 0.1"
            c = ws.cell(row=row, column=start_col + i, value=value)
            c.fill = fill
        row += 1
        # ------------ PROJECT MEETINGS -------------
        ws.cell(row=row, column=start_col - 1, value="Project meetings")
        for i, name in enumerate(ordered_FIRSTNAMES):
            c = ws.cell(row=row, column=start_col + i, value=5)
            c.fill = fill
        row += 1
        # ------------ MEETINGS --------------------
        ws.cell(row=row, column=start_col - 1, value="Meetings")
        for i, name in enumerate(ordered_FIRSTNAMES):
            c = ws.cell(row=row, column=start_col + i, value=4)
            c.fill = fill
        row += 1
        # ------------ AVAILABLE --------------------
        c = ws.cell(row=row, column=start_col - 2,)
        c.fill = fill_sum
        c = ws.cell(row=row, column=start_col - 1, value="Available")
        c.fill = fill_sum
        for i, name in enumerate(ordered_FIRSTNAMES):
            col = start_col + i
            col_letter = get_column_letter(col)
            form = f"={col_letter}{row-5}-{col_letter}{row-4}-{col_letter}{row-3}-{col_letter}{row-2}-{col_letter}{row - 1}"
            c = ws.cell(row=row, column=start_col + i, value=form)
            c.fill = fill_sum
        return row + 1

    def get_firstname(issue: dict) -> str | None:
        assignee = issue.get("assignee")
        if not assignee or not assignee.get("displayName"):
            return None
        parts = assignee["displayName"].split(", ")
        return parts[1] if len(parts) > 1 else assignee["displayName"]

    def issue_title(issue: dict) -> str:
        key = issue.get("key", "")
        summary = issue.get("summary", "")
        est = issue.get("estimation", {}).get("original_estimate_hours") or 0
        est_txt = "" if est is None else str(est)
        return f"{key} - {summary} ({est_txt})"

    def write_issue_row(ws, row: int, issue: dict, diff_spent_hours: bool) -> int:
        firstname = get_firstname(issue)

        if firstname == "Renaud":
            return row

        ws.cell(row=row, column=title_col, value=issue_title(issue))

        if firstname and firstname in ordered_FIRSTNAMES:
            col = start_col + ordered_FIRSTNAMES.index(firstname)
            if diff_spent_hours:
                estimated_hours = float(issue.get("estimation", ).get("original_estimate_hours")  or 0)
                raw = issue.get("spent_hours")
                spent_hours = float(raw or 0)
                value = estimated_hours - spent_hours
            else:
                raw = issue.get("remaining_hours")
                value = float(raw or 0)
            ws.cell(row=row, column=col, value=value)

        ws.cell(row=row, column=state_col, value=issue.get("state"))
        return row + 1

    def write_sum_row(ws, row: int, first_row: int) -> int:
        # Somme par colonne (prénoms)
        c = ws.cell(row=row, column=1, value="Sum")
        c.fill = fill_sum
        for i in range(1, start_col):
            cell = ws.cell(row=row, column=i)
            cell.fill = fill_sum
        for i in range(len(ordered_FIRSTNAMES)):
            col = start_col + i
            col_letter = get_column_letter(col)
            cell = ws.cell(row=row, column=col)
            cell.fill = fill_sum
            if row - 1 >= first_row:
                # ws.cell(row=row, column=col, value=f"=SUM({col_letter}{first_row}:{col_letter}{row - 1})")
                cell.value = f"=SUM({col_letter}{first_row}:{col_letter}{row - 1})"
            else:
                # ws.cell(row=row, column=col, value=0)
                cell.value = int(0)

        list_sum_rows.append(row)
        return row + 1

    def write_section(ws, title: str, row: int, jqls: list[str], diff_spent_hours=False) -> int:
        ws.cell(row=row, column=1, value=title)
        for col in range(1, 3 + len(ordered_FIRSTNAMES)):
            c = ws.cell(row=row, column=col)
            c.fill = fill_title
        row += 1
        first_row = row

        for jql in jqls:
            for issue in jm.run_jql(jql):
                row = write_issue_row(ws, row, issue, diff_spent_hours)

        # Ligne SUM
        row = write_sum_row(ws, row, first_row)
        return row

    def write_total_sum(ws, row: int) -> int:
        c = ws.cell(row=row, column=1, value="TOTAL")
        for col in range(1, start_col):
            c = ws.cell(row=row, column=col)
            c.fill = fill_title
        # Total par colonne = somme des sommes intermédiaires
        for i in range(len(ordered_FIRSTNAMES)):
            col = start_col + i
            col_letter = get_column_letter(col)

            if not list_sum_rows:
                c = ws.cell(row=row, column=col, value=0)
                c.fill = fill_title
            else:
                # ex: =SUM(C5,C12,C20)
                refs = ",".join([f"{col_letter}{r}" for r in list_sum_rows])
                c = ws.cell(row=row, column=col, value=f"=SUM({refs})")
                c.fill = fill_title
            # DELTA
            c = ws.cell(row=row+1, column=col, value=f"={col_letter}{available_row}-{col_letter}{row}")
            c.fill = fill_sum
        c = ws.cell(row=row+1, column=1, value="DELTA")
        c.fill = fill_sum
        c = ws.cell(row=row + 1, column=2, value="")
        c.fill = fill_sum
        return row + 2

    # --- Header
    row = 1
    row = write_header(ws1, row)
    dict_statuses = jm.get_dict_statuses()
    done_statuses = ",".join([f'"{s["name"]}"' for s in dict_statuses["Done"]["statuses"]])
    current_sprint_id = sprint_id
    next_sprint = jm.get_next_sprint(jm.cfg.jira_board_id)
    next_sprint_id = next_sprint.get("id")
    project_id = jm.cfg.jira_project_id
    rec_epic_key = jm.cfg.jira_recurrent_epic_key
    # --- Sections

    # ----- RECURRENTS
    jql_recurrents = f"project = {project_id} and sprint = {next_sprint_id} and 'Epic Link' = {rec_epic_key}"
    row = write_section(ws1, "RECURRENTS", row + 1, [jql_recurrents], False)

    # -------PROJECTS
    jql_projects_current = (
        f"project != {project_id} and sprint = {current_sprint_id} and status not in ({done_statuses})"
    )
    jql_projects_next = f"project != {project_id} and sprint = {next_sprint_id}"
    row = write_section(ws1, "PROJECTS", row + 1, [jql_projects_current, jql_projects_next], False)

    # ---- BKL  restants
    jql_bkl = (
        f"project = {project_id} and sprint = {current_sprint_id} "
        f"and status not in ({done_statuses}) and {jm.JIRA_ONLY_STANDARD_TYPES} ORDER BY status ASC"
    )
    row = write_section(ws1, "BKL", row + 1, [jql_bkl], False)

    # ------- NEW BKL
    jql_new_bkl = f"project = {project_id} and sprint = {next_sprint_id} and 'Epic Link' != {rec_epic_key}"
    row = write_section(ws1, "NEW BKL", row + 1, [jql_new_bkl], False)



    # --- TOTAL
    row = write_total_sum(ws1, row)
    medium = Side(style="medium")
    border = Border(left=medium, right=medium, top=medium, bottom=medium )
    for row in ws1[f"A1:J{row-1}"]:
        for cell in row:
            cell.border = border

    ws2 = wb.create_sheet('Estimation VS Reality')
    # --- Header
    row = 1
    row = write_header(ws2, row)
    jql_bkl = (
        f"project = {project_id} and sprint = {current_sprint_id} "
        f"and status in ({done_statuses}) and {jm.JIRA_ONLY_STANDARD_TYPES} ORDER BY status ASC"
    )
    row = write_section(ws2, "BKL", row, [jql_bkl], True)
    jql_not_bkl = (
        f"project != {project_id} and sprint = {current_sprint_id} "
        f"and status in ({done_statuses}) and {jm.JIRA_ONLY_STANDARD_TYPES} ORDER BY status ASC"
    )
    row = write_section(ws2, "PROJECTS", row, [jql_not_bkl], True)


    wb.save(buffer)
    buffer.seek(0)
    return buffer.getvalue()