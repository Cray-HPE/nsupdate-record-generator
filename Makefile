NAME ?= nsupdate-record-generator
CHART_PATH ?= helm
VERSION ?= $(shell cat .version)

HELM_UNITTEST_IMAGE ?= quintush/helm-unittest:3.3.0-0.2.5

all : image chart
chart: chart_package chart_test

image:
	mkdir -p build/image
	docker build --pull ${DOCKER_ARGS} --tag '${NAME}:${VERSION}' .
	docker save -o build/image/nsupdate-record-generator.tar '${NAME}:${VERSION}'

chart_package:
	mkdir -p build/chart
	helm dep up ${CHART_PATH}/${NAME}
	helm package ${CHART_PATH}/${NAME} -d build/chart

chart_test:
	helm lint "${CHART_PATH}/${NAME}"
	docker run --rm -v ${PWD}/${CHART_PATH}:/apps ${HELM_UNITTEST_IMAGE} -3 ${NAME}
