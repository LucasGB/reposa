#!/usr/local/bin/python3.5
import argparse
import os
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

#	user, token = file.read().split(':')
#	users.append(user)
#	tokens.append(token)

print(users)
print(tokens)
		
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

class Crawler:
	def __init__(self, repository, max_concurrency=50):
		self.page = 'https://api.github.com/repos/{}/pulls?state=closed&page=1&per_page=100'.format(repository)
		self.repository = repository
		#self.session = aiohttp.ClientSession(auth=aiohttp.BasicAuth(user, token))
		self.bounded_sempahore = asyncio.BoundedSemaphore(max_concurrency)
		self.accepted_prs = []
		self.rejected_prs = []
		self.sleep_delay = 3
		self.key = 0
		self.user = users[self.key]
		self.token = tokens[self.key]
		# UTC-3 + 5 minutes
		self.auth_lock_timer = datetime.datetime.utcnow() - datetime.timedelta(hours=3) + datetime.timedelta(minutes=5)

	async def _http_request_old(self, url):
		print('Fetching: {}'.format(url))

		async with self.bounded_sempahore:
			try:
				async with aiohttp.ClientSession(auth=aiohttp.BasicAuth(self.user, self.token)) as client:
					async with client.get(url, timeout=30) as response:
						resp =  await response.json()

						if("200" in response.headers["status"]):
							await asyncio.sleep(3)
							return resp
						# If Rate Limit has exceeded, wait for the reset cooldown and try again.
						elif("403" in response.headers["status"] and 'API rate limit exceeded' in resp["message"]):
							print("exceeded")
							reset_time = float(response.headers['X-RateLimit-Reset'])

							reset_time_format = datetime.datetime.fromtimestamp(reset_time)
							print("Reset Time: {}".format(reset_time_format))
							current_time = datetime.datetime.now()
							print("Curr Time: {}".format(current_time))

							# Sleep for reset time + 1 second.
							delta_time = (reset_time_format - current_time).total_seconds() + 1
							print("Rate Limit exceeded. Sleeping for {} seconds.".format(delta_time))
							await asyncio.sleep(delta_time)
							print("woke")
							return await self._http_request(url)
			except Exception as e:
				print('HTTP Exception: {}'.format(e))

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
		print('Fetching: {}'.format(url))

		async with self.bounded_sempahore:
			try:
				async with aiohttp.ClientSession(auth=aiohttp.BasicAuth(self.user, self.token)) as client:
					async with client.get(url, timeout=30) as response:
						resp =  await response.json()
						
						if("200" in response.headers["status"]):
							if(int(response.headers["X-RateLimit-Remaining"]) <= 100):
								self.roll_auth()

							await asyncio.sleep(3)
							return resp
						# If Rate Limit has exceeded, wait for the reset cooldown and try again.
						elif("403" in response.headers["status"] and 'API rate limit exceeded' in resp["message"]):
							print("Exceeded for: {}".format(url))
							reset_time = float(response.headers['X-RateLimit-Reset'])

							reset_time_format = datetime.datetime.fromtimestamp(reset_time)
							print("Reset Time: {}".format(reset_time_format))
							current_time = datetime.datetime.now()
							print("Curr Time: {}".format(current_time))

							# Sleep for reset time + 1 second.
							delta_time = (reset_time_format - current_time).total_seconds() + 1
							print("Rate Limit exceeded. Sleeping for {} seconds.".format(delta_time))
							await asyncio.sleep(delta_time)
							print("woke")
							return await self._http_request(url)
			except Exception as e:
				print('HTTP Exception: {}'.format(e))

	async def verify_presence_of_review_comments(self, pr):
		print('Verifying presence of review comments of #{}.'.format(pr))

		response = await self._http_request('https://api.github.com/repos/{}/pulls/{}/comments'.format(self.repository, pr))
		
		print("PR: {}\nLEN: {}\nRESP: {}".format(pr, len(response), response))
		if(len(response) == 0):
			return False
		else:
			return True
	
	async def verify_acceptance(self, pr):
		print('Verifying acceptance of pull request #{}'.format(pr))

		response = await self._http_request('https://api.github.com/repos/{}/pulls/{}'.format(self.repository, pr))

		if(response["merged_at"] != None):
				return True
		else:
				return False

	async def filter_by_presence_of_changed_files(self, pr):
		print('Verifying presence of changed files in pull request: {}'.format(pr))

		response = await self._http_request('https://api.github.com/repos/{}/pulls/{}'.format(self.repository, pr))

		if response["changed_files"] > 0:
			return True

		return False

	async def extract_review_comments(self, pr):
		status = await self.filter_by_presence_of_changed_files(pr)

		if(status):
			if(await self.verify_presence_of_review_comments(pr) == True):
				return await self.verify_acceptance(pr), pr

	async def extract_single(self):
		futures = []

		futures.append(self.extract_review_comments('35012'))
		futures.append(self.extract_review_comments('35081'))
		futures.append(self.extract_review_comments('34922'))
		futures.append(self.extract_review_comments('35092'))

		for future in asyncio.as_completed(futures):
			try:
				status, pr = await future
				print("Status: {}\nPR: {}".format(status, pr))
				#future = asyncio.as_completed(self.extract_review_comments('35078'))

				if (status == True):
					self.accepted_prs.append(pr)
				elif (status == False):
					self.rejected_prs.append(pr)
			except Exception as e:
					print('Extract Multi Exception: {}'.format(e))

	async def extract_multi_async(self):
		futures = []

		response = await self._http_request(self.page)

		for pr in response:
			futures.append(self.extract_review_comments(str(pr["number"])))

		print(len(futures))
		for future in asyncio.as_completed(futures):
			try:
				status, pr = await future
#				print("Status: {}\nPR: {}".format(status, pr))

				if (status == True):
					self.accepted_prs.append(pr)
				elif (status == False):
					self.rejected_prs.append(pr)
			except Exception as e:
					print('Extract Multi Exception: {}'.format(e))


	async def crawl_async(self):
		while True:
			async with self.bounded_sempahore:
				try:
					await self.extract_multi_async()

					async with aiohttp.ClientSession(auth=aiohttp.BasicAuth(self.user, self.token)) as client:
						async with client.get(self.page, timeout=30) as response:
							print("LINK: ".format(response.links['next']))
							self.page = response.links['next']['url']
						
				except Exception as e:
					print('While True Exception: {}'.format(e))

		print('LEN: {} Accepted PRs: {}'.format(len(self.accepted_prs), self.accepted_prs))
		print('LEN: {} Rejected PRs: {}'.format(len(self.rejected_prs), self.rejected_prs))
	async def crawl_async_old(self):

		await self.loop()

		async with self.bounded_sempahore:
				try:
					#await self.extract_multi_async()
					#await self.extract_single()

					async with aiohttp.ClientSession(auth=aiohttp.BasicAuth(self.user, self.token)) as client:
						async with client.get(self.page, timeout=30) as response:
							self.page = response.links['next']['url']
						
				except Exception as e:
					print('While True Exception: {}'.format(e))

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
		crawler = Crawler('rails/rails')
		#future = asyncio.Task(crawler.crawl_async())
		loop = asyncio.get_event_loop()
		loop.run_until_complete(crawler.crawl_async())
	except: 
		pass
	finally:
		loop.close()
