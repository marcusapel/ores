cd $PSScriptRoot
docker pull community.opengroup.org:5555/osdu/platform/domain-data-mgmt-services/reservoir/open-etp-server/open-etp-server-main:latest
docker tag community.opengroup.org:5555/osdu/platform/domain-data-mgmt-services/reservoir/open-etp-server/open-etp-server-main osdu-etp-client
docker run --rm --network=host --entrypoint=sh osdu-etp-client -c '/bin/openETPServer space -S ws://localhost:9100 --new -s demo/Volve' 
docker run --rm -v .\data\:/data --network=host --entrypoint=sh osdu-etp-client -c '/bin/openETPServer space -S ws://localhost:9100/ -s demo/Volve --import-epc /data/Volve_Demo_Horizons_Depth.epc'
docker run --rm --network=host --entrypoint=sh osdu-etp-client -c '/bin/openETPServer space -S ws://localhost:9100/ -s demo/Volve --stats'