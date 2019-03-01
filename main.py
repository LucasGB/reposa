#!/usr/local/bin/python3.5
import argparse
import os
from SentiCR.SentiCR import  SentiCR
import pickle
import asyncio
import aiohttp
import time
import datetime

users = []
tokens = []
user = ''
token = ''

with open('auth.txt', 'r') as file:
	for line in file.readlines():
		user, token = line.split(':')
		users.append(user.replace('\n', ''))
		tokens.append(token.replace('\n', ''))
		
repositories = []

def init():
	print('Initialising')

	with open('repositories.txt', 'r') as file:
		repositories = file.read().splitlines()

		for repo in repositories:

			full_name = repo.split('/')
			directory = 'repositories/{}_{}'.format(full_name[0], full_name[1])

			if not os.path.exists(directory):
				os.makedirs(directory)

def classify(sentences):

    saved_SentiCR_model = 'classifier_models/SentiCR_model.sav'
    
    if(os.path.exists(saved_SentiCR_model)):
      sentiment_analyzer = pickle.load(open(saved_SentiCR_model, 'rb'))
      print 'Loaded SentiCR model'
    else:
      sentiment_analyzer = SentiCR.SentiCR()
      pickle.dump(sentiment_analyzer, open(saved_SentiCR_model, 'wb'))
      print 'Saved model to file'

    for sent in sentences:
        score = sentiment_analyzer.get_sentiment_polarity(sent)
        print(sent+"\n Score: "+str(score))


class Crawler:
	def __init__(self, repository, max_concurrency=10):
		self.key = 0
		self.user = users[self.key]
		self.token = tokens[self.key]
		self.page = 'https://api.github.com/repos/{}/pulls?state=closed&page=1&per_page=100&client_id={}&client_secret={}'.format(repository, self.user, self.token)
		self.repository = repository
		self.bounded_sempahore = asyncio.BoundedSemaphore(max_concurrency)
		self.accepted_prs = []
		self.rejected_prs = []
		self.sleep_delay = 3
		
		# UTC-3 + 5 minutes
		self.auth_lock_timer = datetime.datetime.utcnow() - datetime.timedelta(hours=3) + datetime.timedelta(minutes=5)

	def roll_auth(self):
		curr_time = datetime.datetime.utcnow() - datetime.timedelta(hours=3)

		# If 5 miutes have been gone by from the last time we rolled the HTTP authentication
		if(curr_time > self.auth_lock_timer):
			# Set the lock to 5 minutes from now
			self.auth_lock_timer = curr_time + datetime.timedelta(minutes=5)
			print("Rolling Authentication")
			if self.key < len(users):
				self.key += 1
			else:
				self.key = 0

			self.user = users[self.key]
			self.token = tokens[self.key]
		else:
			print("Not rolling authentication")

	async def _http_request(self, url):
		await asyncio.sleep(1.5)

		print('Fetching: {}'.format(url))

		async with self.bounded_sempahore:
			try:
				async with aiohttp.ClientSession() as client:
					async with client.get(url, timeout=30) as response:
						resp =  await response.json()
						if("200" in response.headers["status"]):
							if(int(response.headers["X-RateLimit-Remaining"]) <= 100):
								self.roll_auth()

							print("RESP: {}".format(resp))
							return resp
						
						if(response.status == 403):
							if("You have triggered an abuse detection mechanism" in resp["message"]):
								print("ABUSE")
							# If Rate Limit has exceeded, wait for the reset cooldown and try again.
							elif('API rate limit exceeded' in resp["message"]):
								print("Exceeded for {}. URL: {}".format(self.user, url))
								reset_time = float(response.headers['X-RateLimit-Reset'])

								reset_time_format = datetime.datetime.fromtimestamp(reset_time)
								print("Reset Time: {}".format(reset_time_format))
								current_time = datetime.datetime.now()
								print("Curr Time: {}".format(current_time))
								
								#Change credentials and update to new url
								url = url.split('?client_id')[0]
								url += '?client_id={}&client_secret={}'.format(self.user, self.token)
								self.roll_auth()

								print("NEW URL: {}".format(url))
								return await self._http_request(url)
						
			except Exception as e:
				print('HTTP Exception: {}'.format(e))

	async def verify_presence_of_review_comments(self, pr):
		print('Verifying presence of review comments of #{}.'.format(pr))

		response = await self._http_request('https://api.github.com/repos/{}/pulls/{}/comments?client_id={}&client_secret={}'.format(self.repository, pr, self.user, self.token))
		
		print("PR: {}\nLEN: {}\n".format(pr, len(response)))
		if(len(response) == 0):
			return False
		else:
			return True
	
	async def verify_acceptance(self, pr):
		print('Verifying acceptance of pull request #{}'.format(pr))

		response = await self._http_request('https://api.github.com/repos/{}/pulls/{}?client_id={}&client_secret={}'.format(self.repository, pr, self.user, self.token))

		if(response["merged_at"] != None):
				return True
		else:
				return False

	async def filter_by_presence_of_changed_files(self, pr):
		print('Verifying presence of changed files in pull request: {}'.format(pr))

		response = await self._http_request('https://api.github.com/repos/{}/pulls/{}?client_id={}&client_secret={}'.format(self.repository, pr, self.user, self.token))

		if response["changed_files"] > 0:
			return True

		return False

	async def extract_review_comments(self, pr):
		status = await self.filter_by_presence_of_changed_files(pr)

		if(status):
			if(await self.verify_presence_of_review_comments(pr) == True):
				return await self.verify_acceptance(pr), pr

	async def extract_multi_async(self):
		futures = []

		response = await self._http_request(self.page)

		for pr in response:
			futures.append(self.extract_review_comments(str(pr["number"])))

		print(len(futures))
		for future in asyncio.as_completed(futures):
			try:
				status, pr = await future
				print("Status: {}\nPR: {}".format(status, pr))

				if (status == True):
					self.accepted_prs.append(pr)
				elif (status == False):
					self.rejected_prs.append(pr)
			except Exception as e:
					print('Extract Multi Exception: {}'.format(e))


	async def crawl_async(self):

		async with aiohttp.ClientSession() as client:
			async with client.get(self.page, timeout=30) as response:
				last_page_number = int(str(response.links['last']['url']).split("page=")[1].replace('&per_', '')) + 1
		
		
		for i in range(1, last_page_number):
			async with self.bounded_sempahore:
				try:
					self.page = 'https://api.github.com/repos/{}/pulls?state=closed&page={}&per_page=100&client_id={}&client_secret={}'.format(self.repository, i, self.user, self.token)

					await self.extract_multi_async()
						
				except Exception as e:
					print('Crawl for Exception: {}'.format(e))
					
		print('LEN: {} Accepted PRs: {}'.format(len(self.accepted_prs), self.accepted_prs))
		print('LEN: {} Rejected PRs: {}'.format(len(self.rejected_prs), self.rejected_prs))

if __name__ == '__main__':
	parser = argparse.ArgumentParser()
	parser.add_argument("--auth", help="GitHub's API authentication token.\n(Format: <username>:<api token>)")
	args = parser.parse_args()

	if args.auth:
		with open('config/auth.txt', 'w') as file:
			file.write(args.auth)

	init()

	try:
		crawler = Crawler('vuejs/vue')
		#future = asyncio.Task(crawler.crawl_async())
		loop = asyncio.get_event_loop()
		loop.run_until_complete(crawler.crawl_async())
	except: 
		pass
	finally:
		loop.close()
