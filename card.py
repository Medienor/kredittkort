import requests
import xml.etree.ElementTree as ET
import unicodedata
import re
import time
import math
from statistics import mean
from creds import username, password
from weds import webflow_bearer_token
from datetime import datetime, timedelta
import logging

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Define the mapping between XML <f:...> fields and Webflow fieldData fields
field_mapping = {
    'kredittkort_andre_fordeler': 'kredittkort-andre-fordeler',
    'min_alder': 'aldersgrense-2',
    'maks_alder': 'min-alder-2',
    'kredittkort_reiseforsikring': 'reiseforsikring',
    'kredittkort_reiseforsikring_beskrivelse': 'kredittkort-reiseforsikring-beskrivelse-3',
    'leverandor_tekst': 'leverandor-tekst',
    'kredittkort_maks_ramme': 'kredittkort-maks-ramme-2',
    'kredittkort_min_inntekt': 'kredittkort-min-inntekt-2',
    'kredittkort_termingebyr': 'kredittkort-termingebyr-2',
    'kredittkort_nominell_rente': 'kredittkort-nominell-rente-3',
    'kredittkort_rentefri_periode': 'kredittkort-rentefri-periode-2',
    'kredittkort_andre_fordeler_beskrivelse': 'kredittkort-andre-fordeler-beskrivelse-2',
    'kredittkort_reiseforsikring_beskrivelse': 'kredittkort-reiseforsikring-beskrivelse-3',
    'kredittkort_uttak_egen_bank_i_apningstid_transgebyr': 'kredittkort-uttak-egen-bank-i-apningstid-2',
    'kredittkort_uttak_utland_valutapaslag': 'kredittkort-uttak-utland-valutapaslag-2',
    'effektiv_rente': 'effektiv-rente-4',
    'kredittkort_kort_arsgebyr': 'kredittkort-kort-arsgebyr-2',
    'eksempel_rente': 'eksempel-rente',
    'spesielle_betingelser': 'spesielle-betingelser'
}

namespaces = {'atom': 'http://www.w3.org/2005/Atom', 'f': 'http://www.finansportalen.no/feed/ns/1.0'}

def normalize_for_slug(text):
    return re.sub(r'[+%,:&()/.]', '', unicodedata.normalize('NFKD', (text or '').lower().replace('æ', 'a').replace('ø', 'o').replace('å', 'a')).encode('ascii', 'ignore').decode('utf-8').replace(' ', '-')).strip()

def extract_id(entry):
    return entry.find('atom:id', namespaces).text.split('/')[-1]

def get_norwegian_date():
    yesterday = datetime.now() - timedelta(days=1)
    months = ['januar', 'februar', 'mars', 'april', 'mai', 'juni', 'juli', 'august', 'september', 'oktober', 'november', 'desember']
    return f"Oppdatert {yesterday.day}. {months[yesterday.month - 1]} {yesterday.year} - 23:59"

def sanitize_text(text):
    # Replace line breaks and multiple spaces with a single space
    sanitized = re.sub(r'\s+', ' ', text)
    # Trim leading and trailing whitespace
    return sanitized.strip()

def format_norwegian_number(number):
    try:
        # Convert to float and then to integer to remove decimal places
        num = int(float(number))
        # Format with thousands separator
        return f"{num:,}".replace(",", " ")
    except ValueError:
        # If conversion fails, return the original value
        return number

def fetch_webflow_item(item_id):
    headers = {"accept": "application/json", "authorization": f"Bearer {webflow_bearer_token}"}
    response = requests.get(f"https://api.webflow.com/v2/collections/66757dbebe301e2905b5ecc2/items/{item_id}", headers=headers)
    if response.status_code == 200:
        return response.json()
    else:
        logger.error(f"Failed to fetch Webflow item {item_id}. Status code: {response.status_code}")
        return None
    
def calculate_apr(xml_data):
    try:
        nominal_rate = float(xml_data.get('kredittkort_nominell_rente', 0)) / 100
        loan_amount = 30000  # Fixed loan amount as specified
        annual_fee = float(xml_data.get('kredittkort_kort_arsgebyr', 0))
        term_fee = float(xml_data.get('kredittkort_termingebyr', 0))

        # Number of capitalizations per year (monthly)
        n = 12

        # Calculate effective rate based on nominal rate
        effective_rate = math.pow(1 + (nominal_rate / n), n) - 1

        # Calculate total costs for one year
        total_fees = annual_fee + (term_fee * 12)
        total_interest = loan_amount * effective_rate
        total_cost = total_fees + total_interest

        # Calculate adjusted effective rate including fees
        adjusted_effective_rate = total_cost / loan_amount

        # Calculate total amount after interest
        total_amount = loan_amount + total_cost

        # Format the example string
        example = (f"Nom.rente {nominal_rate*100:.2f}%, eff.rente {adjusted_effective_rate*100:.2f}%, "
                   f"lånebeløp {loan_amount}, nedbetalt o/ 12 måneder, "
                   f"kost: {total_cost:.0f}, tot: {total_amount:.0f}.")

        return {
            'effective_rate': f"{adjusted_effective_rate * 100:.2f}%",
            'example': example
        }
    except Exception as e:
        logger.error(f"Error calculating APR and example: {str(e)}")
        return None

def parse_xml_and_process():
    response = requests.get("https://www.finansportalen.no/services/feed/v3/bank/kredittkort.atom", auth=(username, password))
    if response.status_code == 200:
        root = ET.fromstring(response.content)
        entries = root.findall('atom:entry', namespaces)
        total_entries = len(entries)
        
        logger.info(f"Total number of entries in XML: {total_entries}")
        
        xml_entries = [(entry.find('atom:title', namespaces).text.strip(),
                        entry.find('f:leverandor_tekst', namespaces).text.strip() if entry.find('f:leverandor_tekst', namespaces) is not None else '',
                        {elem.tag.split('}')[1]: elem.text.strip() if elem.text else '' for elem in entry.findall('f:*', namespaces)},
                        extract_id(entry)) for entry in entries]
        
        check_webflow_existence(xml_entries, total_entries)
    else:
        logger.error(f"Failed to fetch XML data. Status code: {response.status_code}")

def update_specific_item(slug_id):
    headers = {"accept": "application/json", "authorization": f"Bearer {webflow_bearer_token}", "Content-Type": "application/json"}
    
    # Fetch the item
    item_response = requests.get(f"https://api.webflow.com/v2/collections/66757dbebe301e2905b5ecc2/items/{slug_id}", headers=headers)
    
    if item_response.status_code != 200:
        logger.error(f"Failed to fetch item with slug ID {slug_id}. Status code: {item_response.status_code}")
        return
    
    item = item_response.json()
    
    # Prepare update payload (you may need to adjust this based on your needs)
    update_payload = {
        "isArchived": False,
        "isDraft": False,
        "fieldData": {
            'name': item['fieldData'].get('name', ''),
            'f-leverandor-tekst': item['fieldData'].get('f-leverandor-tekst', ''),
            'sist-oppdatert': get_norwegian_date()
            # Add other fields as needed
        }
    }
    
    # Attempt to update the item
    update_response = requests.patch(
        f"https://api.webflow.com/v2/collections/66757dbebe301e2905b5ecc2/items/{slug_id}/live",
        json=update_payload,
        headers=headers
    )
    
    if update_response.status_code == 200:
        logger.info(f"Successfully updated item with slug ID {slug_id}")
    else:
        logger.error(f"Failed to update item with slug ID {slug_id}. Status code: {update_response.status_code}")   

def check_andre_fordeler(andre_fordeler_beskrivelse):
    andre_fordeler_beskrivelse = andre_fordeler_beskrivelse.lower() if andre_fordeler_beskrivelse else ""
    
    priority_pass = any(keyword in andre_fordeler_beskrivelse for keyword in ["priority pass", "lounge"])
    cashback = any(keyword in andre_fordeler_beskrivelse for keyword in ["cashback", "cash back", "penger tilbake"])
    rabatter = "rabatt" in andre_fordeler_beskrivelse
    bonuser = "bonus" in andre_fordeler_beskrivelse
    
    return {
        "priority-pass": priority_pass,
        "cashback": cashback,
        "rabatter": rabatter,
        "bonuser": bonuser
    }        


def check_webflow_existence(xml_entries, total_entries):
    headers = {"accept": "application/json", "authorization": f"Bearer {webflow_bearer_token}", "Content-Type": "application/json"}
    
    webflow_items = fetch_all_webflow_items()
    successful_updates = 0
    new_items_created = 0
    
    for title, orgnr, xml_data, numerical_id in xml_entries:
        try:
            update_payload = {
                "isArchived": False,
                "isDraft": False,
                "fieldData": {
                    'name': title,
                    'leverandor-tekst': orgnr,
                    'sist-oppdatert': get_norwegian_date()
                }
            }
            
             # Add mapped fields from XML data
            for xml_field, webflow_field in field_mapping.items():
                if xml_field in xml_data:
                    if webflow_field in ['kredittkort-reiseforsikring-beskrivelse-3', 'kredittkort-andre-fordeler-beskrivelse-2']:
                        update_payload['fieldData'][webflow_field] = sanitize_text(xml_data[xml_field])
                    elif webflow_field == 'kredittkort-maks-ramme-2':
                        update_payload['fieldData'][webflow_field] = format_norwegian_number(xml_data[xml_field])
                    else:
                        update_payload['fieldData'][webflow_field] = xml_data[xml_field]

            # Check 'kredittkort_andre_fordeler_beskrivelse' for specific keywords
            andre_fordeler = check_andre_fordeler(xml_data.get('kredittkort_andre_fordeler_beskrivelse', ''))
            update_payload['fieldData'].update(andre_fordeler)

            # Calculate and add effective rate
            apr = calculate_apr(xml_data)
            if apr is not None:
                update_payload['fieldData']['effektiv-rente-4'] = apr['effective_rate']
                update_payload['fieldData']['eksempel-rente'] = apr['example']       

            bank_id = get_bank_id(orgnr)
            if bank_id:
                update_payload['fieldData']['bank'] = bank_id

            webflow_item = webflow_items.get(numerical_id)

            if webflow_item:
                # Update existing item
                update_response = requests.patch(
                    f"https://api.webflow.com/v2/collections/66757dbebe301e2905b5ecc2/items/{webflow_item['id']}/live",
                    json=update_payload,
                    headers=headers
                )

                print(f"Item ID: {numerical_id}, Status Code: {update_response.status_code}")

                if update_response.status_code == 200:
                    successful_updates += 1
            else:
                # Create new item
                create_payload = update_payload.copy()
                create_payload['fieldData']['slug'] = numerical_id  # Set the slug for the new item
                
                create_response = requests.post(
                    f"https://api.webflow.com/v2/collections/66757dbebe301e2905b5ecc2/items/live",
                    json=create_payload,
                    headers=headers
                )

                print(f"New Item ID: {numerical_id}, Status Code: {create_response.status_code}")

                if create_response.status_code == 200:
                    new_items_created += 1
                else:
                    print(f"Failed to create new item: {numerical_id}. Error: {create_response.text}")

        except Exception as e:
            print(f"Item ID: {numerical_id}, Error: {str(e)}")

        time.sleep(1)  # 1 second delay between API calls
    
    print(f"Total successful updates: {successful_updates} out of {total_entries} XML entries")
    print(f"New items created: {new_items_created}")

def fetch_all_webflow_items():
    headers = {"accept": "application/json", "authorization": f"Bearer {webflow_bearer_token}"}
    all_items = {}
    offset = 0
    limit = 100

    while True:
        response = requests.get(
            f"https://api.webflow.com/v2/collections/66757dbebe301e2905b5ecc2/items?limit={limit}&offset={offset}",
            headers=headers
        )
        if response.status_code == 200:
            items = response.json()['items']
            for item in items:
                all_items[item['fieldData'].get('slug', '')] = item
            if len(items) < limit:
                break
            offset += limit
        else:
            print(f"Failed to fetch Webflow items. Status code: {response.status_code}")
            break
        time.sleep(1)

    print(f"Fetched {len(all_items)} unique items from Webflow")
    return all_items

def get_bank_id(orgnr):
    headers = {"accept": "application/json", "authorization": "Bearer a015cb2d28c98a432dd0d7dab54c5dc32861646565d33f42883c78815babb1de"}
    for offset in range(0, 400, 100):
        response = requests.get(f"https://api.webflow.com/v2/collections/66636a29a268f18ba1798b0a/items?limit=100&offset={offset}", headers=headers)
        if response.status_code == 200:
            for item in response.json().get('items', []):
                if item.get('fieldData', {}).get('name') == orgnr:
                    print(f"Found bank ID: {item['id']} for orgnr: {orgnr}")
                    return item['id']
        else:
            print(f"Failed to retrieve data for offset {offset} while fetching bank ID. Status code: {response.status_code}")
    print(f"No bank ID found for orgnr: {orgnr}")
    return None

def main():
    
    # Then proceed with the regular update process
    parse_xml_and_process()

if __name__ == "__main__":
    main()