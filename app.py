#!/usr/bin/env python3
"""
Copyright (c) 2023 Cisco and/or its affiliates.
This software is licensed to you under the terms of the Cisco Sample
Code License, Version 1.1 (the "License"). You may obtain a copy of the
License at
https://developer.cisco.com/docs/licenses
All use of the material herein must be in accordance with the terms of
the License. All rights not expressly granted by the License are
reserved. Unless required by applicable law or agreed to separately in
writing, software distributed under the License is distributed on an "AS
IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express
or implied.
"""

__author__ = "Trevor Maco <tmaco@cisco.com>"
__copyright__ = "Copyright (c) 2023 Cisco and/or its affiliates."
__license__ = "Cisco Sample Code License, Version 1.1"

import datetime
import requests
import math
import xlsxwriter
from io import BytesIO

import meraki
from meraki import APIError
from flask import Flask, request, render_template, Response, jsonify
from rich.console import Console
from rich.panel import Panel

from config import *

# Flask Config
app = Flask(__name__)
app.config['JSON_SORT_KEYS'] = False

# Meraki Dashboard Instance
dashboard = meraki.DashboardAPI(api_key=MERAKI_API_KEY, suppress_logging=True)

# Form Submission Results
usage = {}

# Rich Console Instance
console = Console()


# Methods
def getSystemTimeAndLocation():
    """Returns location and time of accessing device"""
    # request user ip
    userIPRequest = requests.get('https://get.geojs.io/v1/ip.json')
    userIP = userIPRequest.json()['ip']

    # request geo information based on ip
    geoRequestURL = 'https://get.geojs.io/v1/ip/geo/' + userIP + '.json'
    geoRequest = requests.get(geoRequestURL)
    geoData = geoRequest.json()

    # create info string
    location = geoData['country']
    timezone = geoData['timezone']
    current_time = datetime.datetime.now().strftime("%d %b %Y, %I:%M %p")
    timeAndLocation = "System Information: {}, {} (Timezone: {})".format(location, current_time, timezone)

    return timeAndLocation


def convert_to_sec(time_period):
    """
    Convert time period from submission form to seconds (required by Meraki API usage call)
    :param time_period: Specified in submission form (24h, 72h, 1 week, or custom)
    :return: Time period in seconds
    """
    # Default Case (return 24 hours)
    if time_period == '':
        return 24 * 3600
    # Hours case
    elif 'Hours' in time_period:
        hour = int(time_period.split(' ')[0])
        return hour * 3600
    elif 'Week' in time_period:
        week = int(time_period.split(' ')[0])
        return week * 7 * 24 * 3600
    # Custom interval case (hours)
    else:
        return int(time_period) * 3600


def get_network_ids(org_name):
    """
    Get network IDs in org
    :param org_name: Org Name
    :return: Network ID
    """
    # Get Meraki Org ID
    orgs = dashboard.organizations.getOrganizations()
    org_id = ''
    for org in orgs:
        if org['name'] == org_name:
            org_id = org['id']
            break

    if org_id == '':
        return None

    # Get Meraki Network IDs
    networks = dashboard.organizations.getOrganizationNetworks(organizationId=org_id)
    net_ids = [(net_id['id'], net_id['name']) for net_id in networks]

    console.print(f'Found [green]{len(net_ids)}[/] Network ID(s)!')

    return net_ids


def sorted_list_network_names():
    """
    Create list of sorted network names from usage dictionary
    :return: List of sorted Network Names
    """
    # Create list of network names
    network_names = [network['network_name'] for network in usage['networks']]

    # Sort networks alphabetically
    network_names = sorted(network_names, key=lambda d: d.lower())

    return network_names


def app_usage_history(mac_address, network_ids, time_period):
    """
    Return App usage history for client across networks (summary and network specific)
    :param mac_address: Client MAC address
    :param network_ids: Org network ids
    :param time_period: Time period for usage data
    :return: Dictionary containing summary and individual network app usage data for the specific client
    """
    # Build app usage dictionary for each network and summary
    app_usage = {"client_mac": mac_address, "summary": {}, "networks": []}
    for network in network_ids:
        try:
            response = dashboard.networks.getNetworkClientsApplicationUsage(
                network[0], mac_address, timespan=time_period, total_pages='all'
            )
        except APIError as a:
            if 'not found' in a.message['errors'][0]:
                console.print(f'[red]Client Not Found [/] in {network[1]}.')

                # Log Network, and empty applications list
                app_usage['networks'].append({"network_name": network[1], "applications": {}})
                continue
            else:
                return

        # Sort returned applications alphabetically
        applications = sorted(response[0]['applicationUsage'], key=lambda d: d['application'].lower())

        console.print(f"Found usage Data in [blue]{network[1]}[/] for [yellow]{len(applications)} applications![/]")

        # Summarize usage data across networks, track data per network
        net_app_usage = {"network_name": network[1], "applications": {}}

        for application in applications:
            name = application['application']

            # Append to Network Dictionary
            net_app_usage['applications'][name] = [application['received'], application['sent']]

            # Append to Summary Dictionary
            if name not in app_usage['summary']:
                app_usage['summary'][name] = [application['received'], application['sent']]
            else:
                app_usage['summary'][name][0] += application['received']
                app_usage['summary'][name][1] += application['sent']

        app_usage['networks'].append(net_app_usage)

    return app_usage


def calculate_page(network, page):
    """
    Return page of data for the specific network, return pagination display information for navigation
    :param network: Target Network
    :param page: Current Page
    :return: Page information for that network and page
    """
    if network == 'summary':
        target_data = usage['summary']
    else:
        for net in usage['networks']:
            if net['network_name'] == network:
                target_data = net['applications']

    # If empty, no application data found for client in network
    if not target_data:
        return target_data, None

    # Determine total number of pages
    items_per_page = 10
    total_pages = int(math.ceil(len(target_data) / items_per_page))

    # Convert dictionary to a list of tuples for easy slicing
    usage_list = list(target_data.items())

    # Slice data to get items for the requested page
    start_index = (page - 1) * items_per_page
    end_index = start_index + items_per_page
    page_data = dict(usage_list[start_index:end_index])

    # pagination display information
    pagination = {
        'page': page,
        'first_page': 1,
        'last_page': total_pages,
        'previous_page': page - 1 if page - 1 > 0 else 1,
        'next_page': page + 1 if page + 1 < total_pages else total_pages,
        'page_count': total_pages
    }

    return page_data, pagination


# Flask Routes
@app.route('/')
def index():
    """
    Homepage: Clear global usage dictionary on load
    :return:
    """
    global usage

    # Clear usage dictionary
    usage.clear()

    return render_template('index.html', hiddenLinks=False, timeAndLocation=getSystemTimeAndLocation(),
                           table_flag=False)


@app.route('/display', methods=["POST"])
def submit():
    """
    Handle form submission: query Meraki for application usage data for each network, generate summary table,
    return table information to webpage
    :return: A List of tables containing usage data (paginated at 1 by default)
    """
    global usage
    mac_address = request.form['mac_address']
    time_period = request.form['time_period']
    custom_period = request.form['custom-interval']

    console.print(Panel.fit("Submission Detected:"))
    console.print(f"For [blue]{mac_address}[/], with Time Period: [yellow]{time_period}[/], and optional Custom "
                  f"Period: [yellow]{custom_period}[/]")

    # Select custom value if present
    if len(custom_period) != 0:
        seconds = convert_to_sec(custom_period)
    else:
        seconds = convert_to_sec(time_period)

    console.print(Panel.fit(f"Getting Network IDs", title="Step 1"))

    # Get network id's in org
    network_ids = get_network_ids(ORG_NAME)

    console.print(Panel.fit(f"Getting App Usage History", title="Step 2"))

    # Get application information for client mac address across all networks
    usage = app_usage_history(mac_address, network_ids, seconds)

    console.print(Panel.fit(f"Constructing Usage Tables", title="Step 3"))

    # Add Summary information for display
    page_data, pagination = calculate_page('summary', 1)

    # Build new pagination html
    pagination_html = render_template('pagination.html', network_name='summary', pagination=pagination)
    summary_applications = ('summary', page_data, pagination_html)

    # Get sorted list of network names
    network_names = sorted_list_network_names()

    # Add all other network information
    network_applications = []
    for network in network_names:
        page_data, pagination = calculate_page(network, 1)

        # Build new pagination html
        pagination_html = render_template('pagination.html', network_name=network, pagination=pagination)

        network_applications.append((network, page_data, pagination_html))

    # Render template with pagination links and data for the requested page
    return render_template('index.html', hiddenLinks=False, timeAndLocation=getSystemTimeAndLocation(), table_flag=True,
                           network_names=network_names,
                           mac_address=usage['client_mac'], summary_table=summary_applications,
                           network_tables=network_applications)


@app.route('/get_page_data')
def get_page_data():
    """
    Handle AJAX requests for getting different pages of a table
    :return: JSON string of a portion of the table and updated pagination display
    """

    # Parse Params
    network = request.args.get('network', 1, type=str)
    page = request.args.get('page', 2, type=int)

    # Get new table display information for request
    page_data, pagination = calculate_page(network, page)

    # Build new pagination html
    pagination_html = render_template('pagination.html', network_name=network, pagination=pagination)

    return jsonify([page_data, pagination_html])


@app.route('/download')
def download():
    """
    Download Excel file containing summary information seen in WebGUI Tables (event triggered by download button)
    :return: Excel File
    """
    console.print(f"Downloading Excel File [green]meraki_client_app_usage.xlsx[/]")

    # Create in-memory file for writing Excel data
    output = BytesIO()

    # Create Excel workbook and add sheets
    workbook = xlsxwriter.Workbook(output, {'in_memory': True})
    sheets = []

    # Add Summary Sheet
    sheet = workbook.add_worksheet('Summary')
    sheets.append(sheet)

    # Add Network Sheets
    network_names = sorted_list_network_names()
    for name in network_names:
        sheet = workbook.add_worksheet(name)
        sheets.append(sheet)

    # Define Column Headers
    fields = ['Application', 'Received (kilobytes)', 'Sent (kilobytes)']

    for sheet in sheets:
        # Write column headers
        sheet.write_row(0, 0, fields)

        # Write Summary Sheet
        if sheet.name == 'Summary':
            target_dict = usage['summary']
        # Write network sheet
        else:
            for net in usage['networks']:
                if net['network_name'] == sheet.name:
                    target_dict = net['applications']

        for j, (k, v_list) in enumerate(target_dict.items()):
            sheet.write_row(j + 1, 0, [k] + v_list)

    # Set workbook properties
    workbook.set_properties({'title': 'Meraki App Usage'})

    # Close workbook and return Excel file as a response to the request
    workbook.close()

    console.print(f"[green]Download complete![/]")

    # Return Excel file as a response to the request
    response = Response(output.getvalue(), mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    response.headers.set('Content-Disposition', 'attachment', filename='meraki_client_app_usage.xlsx')
    return response


if __name__ == '__main__':
    app.run(port=5000)
