"""Builds and sends the session summary email report (plain-text + HTML table)."""

from datetime import datetime

import config
import gmail_client


def _to_amount(v):
    try:
        return float(str(v).replace("$", "").replace(",", ""))
    except (ValueError, TypeError):
        return 0.0


def _html_table(headers, rows):
    th = "".join(f'<th style="text-align:left;padding:6px 10px;border-bottom:2px solid #ccc;">{h}</th>' for h in headers)
    trs = ""
    for row in rows:
        tds = "".join(f'<td style="padding:6px 10px;border-bottom:1px solid #eee;">{c}</td>' for c in row)
        trs += f"<tr>{tds}</tr>"
    return (
        '<table style="border-collapse:collapse;font-family:Arial,Helvetica,sans-serif;'
        'font-size:13px;margin-bottom:20px;">'
        f"<thead><tr>{th}</tr></thead><tbody>{trs}</tbody></table>"
    )


def send_session_report(sent_items, skipped_items, failed_items):
    """
    sent_items:    list of dicts {ro_number, customer_name, claim_number, total_invoice}
                   for invoices that were actually sent AND posted to A/R this session.
    skipped_items: list of dicts {ro_number, customer_name, reason} — matched
                   criteria but were excluded (e.g. Recon) or already handled.
    failed_items:  list of dicts {ro_number, customer_name, reason} — hit an
                   error partway through (e.g. AR post failed after send).
    """
    if not sent_items and not skipped_items and not failed_items:
        return  # nothing happened this session — no report needed

    total = sum(_to_amount(i.get("total_invoice")) for i in sent_items)
    now_str = datetime.now().strftime("%B %d, %Y %I:%M %p")

    # ---------- plain text (fallback) ----------
    lines = [
        f"Gale — DriveTime Invoicing Summary ({now_str})",
        "",
        f"Invoices sent this session: {len(sent_items)}",
        f"Total amount invoiced: ${total:,.2f}",
        "",
    ]
    if sent_items:
        lines.append("SENT & POSTED TO A/R:")
        for i in sent_items:
            lines.append(
                f"  - RO #{i['ro_number']} | {i['customer_name']} | "
                f"Claim {i.get('claim_number', '?')} | ${_to_amount(i.get('total_invoice')):,.2f}"
            )
        lines.append("")
    if skipped_items:
        lines.append("SKIPPED / EXCLUDED:")
        for i in skipped_items:
            lines.append(f"  - RO #{i['ro_number']} | {i['customer_name']} | {i['reason']}")
        lines.append("")
    if failed_items:
        lines.append("FAILED — NEEDS ATTENTION:")
        for i in failed_items:
            lines.append(f"  - RO #{i['ro_number']} | {i['customer_name']} | {i['reason']}")
        lines.append("")
    body_text = "\n".join(lines)

    # ---------- HTML (tables) ----------
    html_parts = [
        f'<div style="font-family:Arial,Helvetica,sans-serif;font-size:14px;">',
        f"<h2 style=\"margin-bottom:4px;\">Gale — DriveTime Invoicing Summary</h2>",
        f'<p style="color:#555;margin-top:0;">{now_str}</p>',
        f"<p><strong>Invoices sent this session:</strong> {len(sent_items)}"
        f"&nbsp;&nbsp;|&nbsp;&nbsp;<strong>Total amount invoiced:</strong> ${total:,.2f}</p>",
    ]

    if sent_items:
        html_parts.append("<h3>Sent &amp; Posted to A/R</h3>")
        rows = [
            [i["ro_number"], i["customer_name"], i.get("claim_number", "?"), f"${_to_amount(i.get('total_invoice')):,.2f}"]
            for i in sent_items
        ]
        html_parts.append(_html_table(["RO #", "Customer", "Claim #", "Amount"], rows))

    if skipped_items:
        html_parts.append("<h3>Skipped / Excluded</h3>")
        rows = [[i["ro_number"], i["customer_name"], i["reason"]] for i in skipped_items]
        html_parts.append(_html_table(["RO #", "Customer", "Reason"], rows))

    if failed_items:
        html_parts.append('<h3 style="color:#b00020;">Failed — Needs Attention</h3>')
        rows = [[i["ro_number"], i["customer_name"], i["reason"]] for i in failed_items]
        html_parts.append(_html_table(["RO #", "Customer", "Reason"], rows))

    html_parts.append("</div>")
    body_html = "".join(html_parts)

    subject = f"Gale — Invoicing Summary — {datetime.now().strftime('%m/%d/%Y')} (${total:,.2f} sent)"
    gmail_client.send_message(config.GMAIL_SENDER, subject, body_text, body_html=body_html)
