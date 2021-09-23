#!/usr/bin/env python3

import argparse
from dataclasses import dataclass
import datetime
import json
import os
import subprocess
import sys
import time
from typing import Callable, Dict, List, Tuple


@dataclass
class SiteConfig(object):
  name: str
  db_password: str
  hostname: str
  protocol: str
  logs_folder: str
  sql_backup_folder: str
  images_folder: str
  proxy_config_folder: str

  db_service = 'ropewiki_db'
  db_volume = 'ropewiki_database_storage'
  @property
  def db_container(self) -> str:
    return '{}_{}_1'.format(self.name, self.db_service)
  reverse_proxy_service = 'ropewiki_reverse_proxy'

deploy_commands: Dict[str, Callable[[SiteConfig, List[str]], None]] = {}

def deploy_command(func: Callable[[SiteConfig, List[str]], None]):
  global deploy_commands
  deploy_commands[func.__name__] = func
  return func

def make_argparse() -> argparse.ArgumentParser:
  parser = argparse.ArgumentParser(
    usage="%(prog)s [SITE_CONFIG_NAME] [COMMAND]",
    description="Perform a RopeWiki deployment action"
  )
  parser.add_argument(
    'site_config_name',
    metavar='SITE_CONFIG_NAME',
    help='Site configuration name corresponding to SITE_CONFIG_NAME.json located in the site_configs folder')
  parser.add_argument(
    'command',
    metavar='COMMAND',
    choices=list(deploy_commands),
    help='Deployment command to execute: {{{}}}'.format('|'.join(deploy_commands)))
  parser.add_argument(
    'options',
    metavar='OPTIONS',
    nargs='*',
    help='Additional options for certain deployment commands')
  return parser

@dataclass
class UserArgs(object):
  site_config: SiteConfig
  command: str
  options: List[str]

def get_args() -> UserArgs:
  args = make_argparse().parse_args()
  config_name = args.site_config_name
  config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'site_configs', config_name + '.json')
  with open(config_path, 'r') as f:
    config = json.load(f)
  config['name'] = config_name
  config['db_password'] = os.environ.get('WG_DB_PASSWORD', None)
  if not config['db_password']:
    sys.exit('The WG_DB_PASSWORD environment variable must be set')
  return UserArgs(site_config=SiteConfig(**config), command=args.command, options=args.options)

def log(msg: str):
  print("{} {}".format(datetime.datetime.now().isoformat(), msg))

def run_cmd(cmd: str) -> str:
  log('  RUN {}'.format(cmd))
  p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True, shell=True)
  stdout = p.stdout
  if p.returncode != 0:
    print(stdout)
    print('ERROR: Return code {}'.format(p.returncode))
    sys.exit(p.stderr)
  return stdout

def make_docker_compose_script(cmd: str, site_config: SiteConfig) -> Tuple[str, str]:
  docker_compose_command = 'docker-compose -p {name} {cmd}'.format(name=site_config.name, cmd=cmd)
  variable_declarations = '\n'.join([
    'export WG_DB_PASSWORD={db_password}',
    'export WG_HOSTNAME={hostname}',
    'export WG_PROTOCOL={protocol}',
    'export SQL_BACKUP_FOLDER={sql_backup_folder}',
    'export IMAGES_FOLDER={images_folder}',
    'export PROXY_CONFIG_FOLDER={proxy_config_folder}']).format(
    db_password=site_config.db_password,
    hostname=site_config.hostname,
    protocol=site_config.protocol,
    sql_backup_folder=site_config.sql_backup_folder,
    images_folder=site_config.images_folder,
    proxy_config_folder=site_config.proxy_config_folder)
  return variable_declarations, docker_compose_command

def run_docker_compose(cmd: str, site_config: SiteConfig) -> str:
  script = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'docker_compose_command.sh')
  variable_declarations, docker_compose_command = make_docker_compose_script(cmd, site_config)
  with open(script, 'w') as f:
    f.write(variable_declarations + '\n' + docker_compose_command + '\n')
  log('  SCRIPT {}'.format(docker_compose_command))
  return run_cmd('sh {script} && rm {script}'.format(script=script))

def execute_sql(sql_cmd: str, user: str, site_config: SiteConfig):
  run_cmd('docker container exec {db_container} mysql -u{user} -p{password} -e "{cmd}"'.format(
    db_container=site_config.db_container, user=user, password=site_config.db_password, cmd=sql_cmd))

def get_docker_volumes() -> List[str]:
  lines = run_cmd('docker volume ls').split('\n')
  offset = lines[0].index('VOLUME NAME')
  return [line[offset:] for line in lines[1:]]

def latest_sql_backup(site_config: SiteConfig) -> str:
  latest_backup = run_cmd('ls -t {}/*.sql | head -1'.format(site_config.sql_backup_folder)).strip()
  if not latest_backup:
    sys.exit('Could not find latest backup in {}'.format(site_config.sql_backup_folder))
  return latest_backup

@deploy_command
def get_sql_backup_legacy(site_config: SiteConfig, options: List[str]):
    log('Finding latest database backup...')
    latest_backup_zip = run_cmd('ssh root@db01.ropewiki.com "cd /root/backups ; ls -1 -t *.gz | head -1"').strip('\n')
    log('  -> Found {}.'.format(latest_backup_zip))
    latest_backup = latest_backup_zip[:-3]
    local_target = os.path.join(site_config.sql_backup_folder, latest_backup)
    if os.path.exists(local_target):
      log('{} is already present locally at {}'.format(latest_backup, local_target))
      log('  -> Using pre-existing {}.'.format(latest_backup))
    else:
      log('Copying latest database backup locally...')
      run_cmd('mkdir -p {}'.format(site_config.sql_backup_folder))
      log_file = os.path.join(site_config.logs_folder, 'get_sql.log')
      run_cmd('touch {}'.format(log_file))
      zip_target = os.path.join(site_config.sql_backup_folder, latest_backup_zip)
      cmd = (
        'rsync -arv' +
        ' root@db01.ropewiki.com:/root/backups/{} {}'.format(latest_backup_zip, zip_target) +
        ' 2>&1 | tee {}'.format(log_file))
      run_cmd(cmd)
      log('  -> Copied.')
      log('Unzipping {}...'.format(latest_backup_zip))
      run_cmd('gunzip -f {}'.format(zip_target))
      log('  -> Unzipped {}.'.format(latest_sql_backup(site_config)))

@deploy_command
def print_latest_sql_backup(site_config: SiteConfig, options: List[str]):
  print(latest_sql_backup(site_config))

@deploy_command
def get_images_legacy(site_config: SiteConfig, options: List[str]):
  """Retrieve latest files in /images folder from remote server at ropewiki.com

  Requires SSH access to ropewiki.com.
  """

  # NOTE: this requires public key authentication to the remote server
  log("Copying latest /images content locally...")
  log_file = os.path.join(site_config.logs_folder, 'images_backup.log')
  run_cmd('touch {}'.format(log_file))
  cmd = (
    'rsync -arv' +
    ' root@ropewiki.com:/usr/share/nginx/html/ropewiki/images/' +
    ' {}'.format(site_config.images_folder) +
    ' 2>&1 > {}'.format(log_file))
  run_cmd(cmd)
  log('  -> Latest /images content copied locally.')

@deploy_command
def create_db(site_config: SiteConfig, options: List[str]):
  """Intended to be run once to create an empty database while deploying a production system.

  It sets up the ropewiki_db service defined in docker-compose.yaml"""
  log('Deleting/cleaning up any existing database...')

  # Ensure the database is down
  run_docker_compose('stop {db_service}'.format(db_service=site_config.db_service), site_config)

  # Clean up any existing volumes
  run_docker_compose('rm -v -f {db_service}'.format(db_service=site_config.db_service), site_config)
  volumes = get_docker_volumes()
  if site_config.db_volume in volumes:
    run_cmd('docker volume rm {db_volume}'.format(db_volume=site_config.db_volume))

  # Bring the database up
  run_docker_compose('up -d {db_service}'.format(db_service=site_config.db_service), site_config)

  # Wait for container to come up
  log('>> Waiting for MySQL database to initialize...')
  time.sleep(10)
  while True:
    db_status = run_cmd('docker inspect --format "{{{{.State.Status}}}}" {}'.format(site_config.db_container)).strip()
    if db_status == 'running':
      break
    log('  {}'.format(db_status))
    time.sleep(10)

  # Create an empty ropewiki database
  log('>> Creating empty ropewiki database...')
  run_cmd(
    'docker container exec {}'.format(site_config.db_container) +
    ' mysqladmin -u root -p{} create ropewiki'.format(site_config.db_password))

  # Create the ropewiki user
  log('>> Creating ropewiki user...')
  execute_sql("CREATE USER 'ropewiki'@'%' IDENTIFIED BY '{}';".format(
    site_config.db_password), 'root', site_config)
  execute_sql("GRANT ALL PRIVILEGES ON * . * TO 'ropewiki'@'%';", 'root', site_config)
  execute_sql("FLUSH PRIVILEGES;", 'root', site_config)

  log('RopeWiki database initialized successfully.')

@deploy_command
def restore_db(site_config: SiteConfig, options: List[str]):
  """Restore content from a .sql backup file into the database described in docker-compose.yaml.
  """

  latest_backup = latest_sql_backup(site_config)

  log('Restoring backup {}...'.format(latest_backup))
  cmd = ('cat {latest_backup} | ' +
         'docker container exec -i {db_container} mysql -uropewiki -p{db_password} ropewiki').format(
           latest_backup=latest_backup, db_container=site_config.db_container, db_password=site_config.db_password)
  run_cmd(cmd)
  log('  -> Backup restored.')

@deploy_command
def start_site(site_config: SiteConfig, options: List[str]):
  run_cmd('mkdir -p {}'.format(site_config.proxy_config_folder))
  run_docker_compose('up -d', site_config)

@deploy_command
def enable_tls(site_config: SiteConfig, options: List[str]):
  variable_declarations, docker_compose_command = make_docker_compose_script(
    'exec {} certbot --nginx'.format(site_config.reverse_proxy_service), site_config)
  script = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'enable_tls.sh')
  with open(script, 'w') as f:
    f.write(variable_declarations + '\n' + docker_compose_command + '\n')
  log('Script generated.  To enable TLS, run:')
  log('  sh {}'.format(script))

@deploy_command
def renew_certs(site_config: SiteConfig, options: List[str]):
  log('Starting cert renewal check...')
  response = run_docker_compose('exec {reverse_proxy_container} certbot renew'.format(
    reverse_proxy_container=site_config.reverse_proxy_service), site_config)
  log('Renewal check complete; results:')
  print(response)

@deploy_command
def add_cert_cronjob(site_config: SiteConfig, options: List[str]):
  deploy_tool = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'deploy_tool.py')
  cert_renewal_log = os.path.join(site_config.logs_folder, 'cert_renewals.log')
  cmd_to_run = ('export WG_DB_PASSWORD={db_password} && ' +
                'python3 {deploy_tool} {site_name} renew_certs >> {cert_renewal_log} 2>&1').format(
    db_password=site_config.db_password, deploy_tool=deploy_tool, site_name=site_config.name,
    cert_renewal_log=cert_renewal_log)
  run_cmd('crontab -l | {{ cat; echo "0 */12 * * * {cmd}"; }} | crontab -'.format(cmd=cmd_to_run))

@deploy_command
def dc(site_config: SiteConfig, options: List[str]):
  dc_commands = {
    'build', 'bundle', 'config', 'create', 'down', 'events', 'exec', 'help', 'images', 'kill', 'logs', 'pause', 'port',
    'ps', 'pull', 'push', 'restart', 'rm', 'run', 'scale', 'start', 'stop', 'top', 'unpause', 'up', 'version'}
  if not options:
    raise ValueError('Usage: deploy_tool.py dc {{{}}}'.format('|'.join(dc_commands)))
  output = run_docker_compose(' '.join(options), site_config)
  log('Command complete; results:')
  print(output)

def main():
  args = get_args()
  if args.command not in deploy_commands:
    sys.exit('Command not recognized: {}'.format(args.command))
  deploy_commands[args.command](args.site_config, args.options)

if __name__ == '__main__':
  main()