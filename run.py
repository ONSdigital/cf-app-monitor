#!/usr/bin/env python3

from cloudfoundry_client.client import CloudFoundryClient
from sys import argv
import os
import time
import subprocess
import requests
import threading
import json
import time
from collections import OrderedDict
from jinja2 import Environment, FileSystemLoader
from flask import request, jsonify, make_response, Flask, redirect, url_for

app = Flask(__name__)
app.config['TEMPLATES_AUTO_RELOAD'] = True

matrix = OrderedDict()
spaces = OrderedDict()
urls = OrderedDict()
env = Environment(loader=FileSystemLoader('templates'))
scanning = False

try:
	with open('xredentials.json') as io:
		credentials = json.loads(io.read())
		configured = True

except FileNotFoundError:
	configured = False

except Exception as e:
	print(e)
	exit()


class Refresh(object):

	def __init__(self, auth, interval=30):

		self._interval = interval
		self._auth = auth
		self._scanning = False
		thread = threading.Thread(target=self.run, args=())
		thread.daemon = True
		thread.start()

	def run(self):	
		"""
		Regenerate our datapoints by querying all the /info endpoints
		"""
		global matrix, spaces, scanning

		client = CloudFoundryClient(self._auth.get('gate'), skip_verification=True)
		client.init_with_user_credentials(self._auth.get('user'), self._auth.get('pass'))

		for organization in client.organizations:
			org_name = organization['entity']['name']
			for space in organization.spaces():
				space_name = space['entity']['name']
				if space_name not in spaces:
					spaces[space_name] = 0
				for app in space.apps():
					name = app['entity']['name']
					if name.split('-')[-1] not in spaces:
						continue
					app_name = '-'.join(app['entity']['name'].split('-')[:-1])
					route = app.summary()['routes']
					if not len(route):
						continue
					domain = route[0]['domain']['name']
					host = route[0]['host']
					url = 'http://{}.{}/info'.format(host, domain)
					response = requests.get(url)
					try:
						if app_name not in matrix:
							matrix[app_name] = {}
							urls[app_name] = {}
						urls[app_name][space_name] = url
						matrix[app_name][space_name] = response.json()
						spaces[space_name] += 1
					except Exception as e:
						pass

		if scanning:
			return

		scanning = True

		while True:
			time.sleep(10)
			count = 0
			immutable_matrix = list(matrix)
			for app in immutable_matrix:
				immutable_spaces = list(spaces)
				for space in immutable_spaces:
					if space in urls[app]:
						url = urls[app][space]
						response = requests.get(url)
						if 'gateway-test' in url:
							print(url, response)
						count += 1
						try:
							json = response.json()
						except Exception as e:							
							json = {'branch': 'ERROR', 'version': str(response.status_code)}

						matrix[app][space] = json

			print('Finished pass, scanned "{}" urls'.format(count))

@app.route('/authenticate')
def authenticate():

	global configured

	credentials = request.args.get('credentials')
	try:
		credentials = json.loads(credentials)
		for auth in credentials:
			Refresh(auth)
		configured = True
	except Exception as e:
		template = env.get_template('loginfail.html')
		return make_response(template.render(), 200)

	return redirect(url_for('home'))

@app.route('/status')
def status():
	"""
	Provide the updated environment
	"""
	global matrix, spaces

	column_titles = ['Micro-Service']
	items = []
	for space in sorted(spaces):
		if spaces[space]:
			column_titles.append(space)

	for app in matrix:
		item = [app]
		if not len(matrix[app]):
			continue
		for space in spaces:
			if not spaces[space]:
				continue
			if not space in matrix[app]:
				item.append('-')
				continue
			j = matrix[app][space]
			branch = j.get('branch', '-')
			if '/' in branch:
				branch = ' '.join(branch.split('/')[1:])
			if not len(branch):
				branch='(unknown)'
			version = j.get('version', '-').replace('-SNAPSHOT', '')
			label = '{}/{}'.format(branch, version)
			if label == 'ERROR/200':
				label = 'UP - no endpoint'
			if label == 'ERROR/404':
				label = 'DOWN or no endpoint'
			item.append(label)
		if len(item):
			items.append(item)

	result = {
	    'draw': 1,
	    'recordsTotal': len(items),
	    'recordsFiltered': len(items),
	    'data': items,
	    'column_titles': column_titles
	}
	return make_response(jsonify(result), 200)

@app.route('/')
def home():
	"""
	Render the status template
	"""
	if configured:
		template = env.get_template('index.html')
	else:
		template = env.get_template('login.html')

	return make_response(template.render(), 200)


if configured:
	for auth in credentials:
		Refresh(auth)

app.run(host='0.0.0.0', port=8080, debug=False)
