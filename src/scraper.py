"""
KabaGuard - Async Web Scraper Module
Handles attendance status checking from the company portal.
"""

import aiohttp
import asyncio
from bs4 import BeautifulSoup
from enum import Enum
from datetime import date
from typing import Optional, Tuple, Dict
import logging
import os
import re
from urllib.parse import urljoin

logger = logging.getLogger(__name__)

# Specialized debug logger explicitly designed to capture exact HTML for NO_RECORD scenarios
debug_logger = logging.getLogger("scraper_debug")
debug_logger.setLevel(logging.DEBUG)
if not debug_logger.handlers:
    os.makedirs('logs', exist_ok=True)
    fh = logging.FileHandler('logs/scraper_debug.log', encoding='utf-8')
    fh.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
    debug_logger.addHandler(fh)


class AttendanceStatus(Enum):
    """Enumeration of possible attendance statuses."""
    CLOCKED_IN = "clocked_in"
    CLOCKED_OUT = "clocked_out"
    NO_RECORD = "no_record"
    ERROR = "error"


def _date_formats() -> list[str]:
    """Return portal date formats to try, with env override support."""
    configured = os.getenv("PORTAL_DATE_FORMATS")
    if configured:
        return [fmt.strip() for fmt in configured.split(",") if fmt.strip()]

    # The portal's NewCssCal('demo1') datepicker defaults to MMDDYYYY.
    # Keep common alternatives as fallbacks for configuration changes.
    return ["%m-%d-%Y", "%d-%m-%Y", "%m%d%Y", "%d%m%Y", "%d/%m/%Y", "%m/%d/%Y", "%Y-%m-%d", "%d-%b-%Y", "%d-%B-%Y"]


def _has_portal_validation_error(soup: BeautifulSoup) -> bool:
    """Detect portal-side validation pages so they are not treated as no record."""
    page_text = soup.get_text(" ", strip=True).lower()
    raw_html = str(soup).lower()
    validation_patterns = [
        "invalid date format",
        "invalid date",
    ]
    return any(pattern in page_text or pattern in raw_html for pattern in validation_patterns)


def _extract_candidate_rows(soup: BeautifulSoup) -> list:
    """Find rows that could belong to the attendance result table."""
    results_span = soup.find('span', {'id': 'ContentPlaceHolder1_Label10'})
    containers = [results_span] if results_span else []

    if not containers:
        containers = soup.find_all('table')

    rows = []
    for container in containers:
        for row in container.find_all('tr'):
            if row.get('bgcolor') == 'lightblue':
                continue

            cells = row.find_all('td')
            if len(cells) < 3:
                continue

            cell_texts = [cell.get_text(" ", strip=True) for cell in cells]
            joined = " ".join(cell_texts).upper()

            # Keep likely result rows and skip decorative/layout tables.
            if "CLOCK" in joined or any(
                header in joined for header in ("FULL NAME", "ID NO", "EVENT", "TRANSACTION DATE", "LOCATION")
            ):
                rows.append(row)

    return rows


def _parse_attendance_rows(rows: list) -> Tuple[AttendanceStatus, Dict]:
    """Parse attendance rows regardless of minor column/order differences."""
    has_clock_in = False
    has_clock_out = False
    details = {'clock_in': None, 'clock_out': None}

    for row in rows:
        cells = row.find_all('td')
        cell_texts = [cell.get_text(" ", strip=True) for cell in cells]

        # Header rows can pass the candidate filter; ignore them here.
        if not cell_texts or any(text.upper() == "EVENT" for text in cell_texts):
            continue

        event_idx = None
        event_text = ""
        for idx, text in enumerate(cell_texts):
            normalized = re.sub(r"[\s-]+", "_", text.upper())
            if "CLOCK_IN" in normalized or "CLOCK_OUT" in normalized:
                event_idx = idx
                event_text = normalized
                break

        if event_idx is None:
            continue

        event_details = {
            'time': cell_texts[event_idx + 1] if len(cell_texts) > event_idx + 1 else "Unknown",
            'location': cell_texts[event_idx + 2] if len(cell_texts) > event_idx + 2 else "Unknown"
        }

        if "CLOCK_IN" in event_text:
            has_clock_in = True
            details['clock_in'] = event_details

        if "CLOCK_OUT" in event_text:
            has_clock_out = True
            details['clock_out'] = event_details

    if has_clock_out:
        return AttendanceStatus.CLOCKED_OUT, details
    if has_clock_in:
        return AttendanceStatus.CLOCKED_IN, details
    return AttendanceStatus.NO_RECORD, details


async def check_attendance_async(
    session: aiohttp.ClientSession,
    kaba_id: str,
    check_date: date,
    portal_url: str
) -> Tuple[AttendanceStatus, Optional[Dict]]:
    """
    Check attendance status for a user on a specific date from the Kaba portal.
    
    The portal requires submitting a form with ID and date, then parses the
    resulting table to find CLOCK_IN and CLOCK_OUT events.
    
    Args:
        session: Active aiohttp ClientSession
        kaba_id: User's Kaba ID
        check_date: Date to check attendance for
        portal_url: Company portal URL
        
    Returns:
        AttendanceStatus enum value:
        - CLOCKED_OUT: Both CLOCK_IN and CLOCK_OUT found
        - CLOCKED_IN: Only CLOCK_IN found
        - NO_RECORD: No attendance records found
    """
    max_retries = 3
    date_formats = _date_formats()
    
    for attempt in range(1, max_retries + 1):
        for date_format in date_formats:
            formatted_date = check_date.strftime(date_format)
            try:
                status, details = await _check_attendance_with_date_format(
                    session,
                    kaba_id,
                    formatted_date,
                    portal_url,
                    attempt,
                    max_retries,
                    date_format,
                )
                if status != AttendanceStatus.ERROR:
                    return status, details

            except aiohttp.ClientError as e:
                logger.error(f"Network error on attempt {attempt}/{max_retries} while checking attendance for {kaba_id}: {e}")
                break

            except Exception as e:
                logger.error(f"Unexpected error in check_attendance_async for {kaba_id}: {e}")
                logger.exception(e)
                return AttendanceStatus.ERROR, None

        if attempt < max_retries:
            logger.info(f"Sleeping for {2 ** attempt} seconds before retrying...")
            await asyncio.sleep(2 ** attempt)

    return AttendanceStatus.ERROR, None


async def _check_attendance_with_date_format(
    session: aiohttp.ClientSession,
    kaba_id: str,
    formatted_date: str,
    portal_url: str,
    attempt: int,
    max_retries: int,
    date_format: str,
) -> Tuple[AttendanceStatus, Optional[Dict]]:
    """Submit one portal request with one formatted date."""
    try:
            logger.info(f"Checking attendance for Kaba ID {kaba_id} on {formatted_date} (Attempt {attempt}/{max_retries})")
            logger.debug(f"Trying portal date format: {date_format}")
            logger.debug(f"Portal URL: {portal_url}")
            
            # ASP.NET portals require getting the form first to extract hidden fields
            logger.info("Step 1: Fetching form page to get ASP.NET hidden fields...")
            
            async with session.get(portal_url, timeout=aiohttp.ClientTimeout(total=45)) as initial_response:
                if initial_response.status != 200:
                    logger.error(f"Failed to fetch form page: status {initial_response.status}")
                    raise aiohttp.ClientError(f"HTTPStatusError: {initial_response.status}")
                
                initial_html = await initial_response.text()
                initial_soup = BeautifulSoup(initial_html, 'html.parser')
                
                # Extract ASP.NET hidden fields
                viewstate = initial_soup.find('input', {'name': '__VIEWSTATE'})
                viewstate_generator = initial_soup.find('input', {'name': '__VIEWSTATEGENERATOR'})
                event_validation = initial_soup.find('input', {'name': '__EVENTVALIDATION'})
                form = initial_soup.find('form')
                
                if not viewstate:
                    logger.error("Could not find __VIEWSTATE in form")
                    raise aiohttp.ClientError("Missing __VIEWSTATE element")

                submit_url = str(initial_response.url)
                if form and form.get('action'):
                    submit_url = urljoin(str(initial_response.url), form['action'])
                
                logger.info("Retrieved ASP.NET hidden fields")
            
            # Prepare form data with ASP.NET field names
            form_data = {
                '__VIEWSTATE': viewstate['value'],
                '__VIEWSTATEGENERATOR': viewstate_generator['value'] if viewstate_generator else '',
                '__EVENTVALIDATION': event_validation['value'] if event_validation else '',
                'ctl00$ContentPlaceHolder1$TextBox1': kaba_id,  # ID Number field
                'demo1': formatted_date,  # Date picker field
                'idd': formatted_date,  # Alternative date field name
                'ctl00$ContentPlaceHolder1$Button1': 'View My Attendance',  # Submit button
            }
            
            logger.info(f"Step 2: Submitting form with Kaba ID {kaba_id} and date {formatted_date}")
            logger.debug(f"Resolved form submit URL: {submit_url}")
            logger.debug(f"Form data keys: {list(form_data.keys())}")
            
            # Submit the form with POST (increase timeout as portal might be slow)
            headers = {
                'Content-Type': 'application/x-www-form-urlencoded',
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                'Referer': portal_url,
            }
            
            async with session.post(
                submit_url,
                data=form_data,
                timeout=aiohttp.ClientTimeout(total=60),  # Increased timeout
                headers=headers,
                allow_redirects=True  # Follow redirects if any
            ) as response:
                logger.info(f"POST request returned status: {response.status}")
                
                if response.status != 200:
                    logger.error(f"Form submission failed with status {response.status}")
                    return AttendanceStatus.ERROR, None
                
                html = await response.text()
                
                # Parse the HTML response
                soup = BeautifulSoup(html, 'html.parser')
                
                if _has_portal_validation_error(soup):
                    logger.warning(f"Portal rejected submitted data for kaba_id={kaba_id}, date={formatted_date}")
                    debug_logger.debug(f"[{kaba_id}][{formatted_date}] PORTAL VALIDATION ERROR. Date format tried: {date_format}")
                    debug_logger.debug(f"--- START RAW HTML ({len(html)} bytes) ---\n{html}\n--- END RAW HTML ---")
                    return AttendanceStatus.ERROR, None

                results_span = soup.find('span', {'id': 'ContentPlaceHolder1_Label10'})
                rows = _extract_candidate_rows(soup)
                logger.info(f"Found {len(rows)} candidate attendance rows")
                
                if not rows:
                    if not results_span:
                        logger.warning(
                            f"Portal returned no result container for kaba_id={kaba_id}, date={formatted_date}; "
                            "treating as scraper error instead of no record."
                        )
                        debug_logger.debug(f"[{kaba_id}][{formatted_date}] SCRAPER ERROR: No result container or candidate rows. Date format tried: {date_format}")
                        debug_logger.debug(f"--- START RAW HTML ({len(html)} bytes) ---\n{html}\n--- END RAW HTML ---")
                        return AttendanceStatus.ERROR, None

                    logger.info(f"No attendance records found for kaba_id={kaba_id}")
                    debug_logger.debug(f"[{kaba_id}][{formatted_date}] NO_RECORD TRIGGERED: No candidate attendance rows were found.")
                    debug_logger.debug(f"--- START RAW HTML ({len(html)} bytes) ---\n{html}\n--- END RAW HTML ---")
                    return AttendanceStatus.NO_RECORD, None

                status, details = _parse_attendance_rows(rows)
                if status == AttendanceStatus.CLOCKED_OUT:
                    logger.info(f"User {kaba_id} has CLOCKED_OUT")
                    return AttendanceStatus.CLOCKED_OUT, details
                if status == AttendanceStatus.CLOCKED_IN:
                    logger.info(f"User {kaba_id} has CLOCKED_IN (not yet clocked out)")
                    return AttendanceStatus.CLOCKED_IN, details

                logger.info(f"User {kaba_id} has NO_RECORD")
                debug_logger.debug(f"[{kaba_id}][{formatted_date}] NO_RECORD TRIGGERED: Candidate rows did not contain CLOCK_IN/CLOCK_OUT.")
                for idx, row in enumerate(rows):
                    r_cells = row.find_all('td')
                    cell_texts = [c.get_text(" ", strip=True) for c in r_cells]
                    debug_logger.debug(f"Row {idx}: {cell_texts}")

                debug_logger.debug(f"--- START RAW HTML ({len(html)} bytes) ---\n{html}\n--- END RAW HTML ---")
                return AttendanceStatus.NO_RECORD, details

    except aiohttp.ClientError:
        raise
