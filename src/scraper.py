"""
KabaGuard - Async Web Scraper Module
Handles attendance status checking from the company portal.
"""

import aiohttp
from bs4 import BeautifulSoup
from enum import Enum
from datetime import date
from typing import Optional, Tuple, Dict
import logging

logger = logging.getLogger(__name__)


class AttendanceStatus(Enum):
    """Enumeration of possible attendance statuses."""
    CLOCKED_IN = "clocked_in"
    CLOCKED_OUT = "clocked_out"
    NO_RECORD = "no_record"
    ERROR = "error"


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
    try:
        # Format date for the portal (MM/DD/YYYY format based on screenshot)
        formatted_date = check_date.strftime("%m/%d/%Y")
        
        logger.info(f"Checking attendance for Kaba ID {kaba_id} on {formatted_date}")
        logger.debug(f"Portal URL: {portal_url}")
        
        # ASP.NET portals require getting the form first to extract hidden fields
        logger.info("Step 1: Fetching form page to get ASP.NET hidden fields...")
        
        async with session.get(portal_url, timeout=aiohttp.ClientTimeout(total=30)) as initial_response:
            if initial_response.status != 200:
                logger.error(f"Failed to fetch form page: status {initial_response.status}")
                return AttendanceStatus.ERROR, None
            
            initial_html = await initial_response.text()
            initial_soup = BeautifulSoup(initial_html, 'html.parser')
            
            # Extract ASP.NET hidden fields
            viewstate = initial_soup.find('input', {'name': '__VIEWSTATE'})
            viewstate_generator = initial_soup.find('input', {'name': '__VIEWSTATEGENERATOR'})
            event_validation = initial_soup.find('input', {'name': '__EVENTVALIDATION'})
            
            if not viewstate:
                logger.error("Could not find __VIEWSTATE in form")
                return AttendanceStatus.ERROR, None
            
            logger.info("✅ Retrieved ASP.NET hidden fields")
        
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
        logger.debug(f"Form data keys: {list(form_data.keys())}")
        
        # Submit the form with POST (increase timeout as portal might be slow)
        headers = {
            'Content-Type': 'application/x-www-form-urlencoded',
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Referer': portal_url,
        }
        
        async with session.post(
            portal_url, 
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
            
            # The portal returns malformed HTML - table rows are in a span!
            # Look for span id="ContentPlaceHolder1_Label10" which contains the results
            results_span = soup.find('span', {'id': 'ContentPlaceHolder1_Label10'})
            
            if results_span:
                logger.info(f"✅ Found results in ContentPlaceHolder1_Label10 span")
                # Parse table rows from the span
                rows = results_span.find_all('tr')
                # Filter out header rows (bgcolor=lightblue)
                rows = [row for row in rows if row.get('bgcolor') != 'lightblue']
            else:
                # Fallback: try finding regular table
                logger.info("Trying fallback: looking for regular table element")
                table = soup.find('table')
                
                if not table:
                    logger.warning(f"No results found for kaba_id={kaba_id}")
                    return AttendanceStatus.NO_RECORD, None
                
                rows = table.find_all('tr')[1:]  # Skip header row
            
            logger.info(f"📊 Found {len(rows)} data rows (excluding header)")
            
            if not rows:
                logger.info(f"No attendance records found for kaba_id={kaba_id}")
                return AttendanceStatus.NO_RECORD, None
            
            # Track what events we found for this date
            has_clock_in = False
            has_clock_out = False
            
            details = {'clock_in': None, 'clock_out': None}
            
            for row in rows:
                cells = row.find_all('td')
                
                if len(cells) < 4:
                    continue  # Skip invalid rows
                
                # Log row contents for debugging
                cell_texts = [cell.get_text(strip=True) for cell in cells]
                logger.debug(f"Row: {cell_texts}")
                
                # Extract the event type (3rd column)
                event = cells[2].get_text(strip=True).upper()
                logger.debug(f"Event column text: '{event}'")
                
                # Check for CLOCK_IN or CLOCK_OUT events
                if 'CLOCK_IN' in event or 'CLOCK IN' in event:
                    has_clock_in = True
                    details['clock_in'] = {
                        'time': cells[3].get_text(strip=True) if len(cells) > 3 else "Unknown",
                        'location': cells[4].get_text(strip=True) if len(cells) > 4 else "Unknown"
                    }
                    logger.info(f"✅ Found CLOCK_IN for kaba_id={kaba_id}")
                
                if 'CLOCK_OUT' in event or 'CLOCK OUT' in event:
                    has_clock_out = True
                    details['clock_out'] = {
                        'time': cells[3].get_text(strip=True) if len(cells) > 3 else "Unknown",
                        'location': cells[4].get_text(strip=True) if len(cells) > 4 else "Unknown"
                    }
                    logger.info(f"✅ Found CLOCK_OUT for kaba_id={kaba_id}")
            
            # Determine status based on what we found
            if has_clock_out:
                # If we found clock out, it means they completed their shift
                logger.info(f"User {kaba_id} has CLOCKED_OUT")
                return AttendanceStatus.CLOCKED_OUT, details
            elif has_clock_in:
                # Only clock in, still in the building
                logger.info(f"User {kaba_id} has CLOCKED_IN (not yet clocked out)")
                return AttendanceStatus.CLOCKED_IN, details
            else:
                # No records found
                logger.info(f"User {kaba_id} has NO_RECORD")
                return AttendanceStatus.NO_RECORD, details
            
    except aiohttp.ClientError as e:
        logger.error(f"Network error while checking attendance for {kaba_id}: {e}")
        # In case of network error, return ERROR to avoid false alerts
        return AttendanceStatus.ERROR, None
    
    except Exception as e:
        logger.error(f"Unexpected error in check_attendance_async for {kaba_id}: {e}")
        logger.exception(e)  # Log full stack trace
        return AttendanceStatus.ERROR, None
