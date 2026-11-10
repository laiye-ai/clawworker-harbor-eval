ARG TEMPLATE_IMAGE
FROM ${TEMPLATE_IMAGE}
USER root
# A local fixture account, not a cloud account or preconfirmed answers.
ARG FIXTURE_OWNER=0123456789abcdef0123456789abcdef
ENV CLAWWORKER_OWNER_ID=${FIXTURE_OWNER}
RUN install -d -m 0755 -o server -g server /workspace "/workspace/native-user/${CLAWWORKER_OWNER_ID}/workspaces" \
    && rmdir /workspace/work \
    && install -d -o server -g server /opt/data/harbor/external_skills/template \
    && cp -a /opt/clawworker-harbor/definition/skills/. /opt/data/harbor/external_skills/template/ \
    && chown -R server:server /opt/data/harbor/external_skills/template
COPY --chmod=0644 ap-onboarding-env.sh /opt/clawworker-harbor/ap-onboarding-env.sh
ENV BASH_ENV=/opt/clawworker-harbor/ap-onboarding-env.sh
USER server
WORKDIR /workspace
ENTRYPOINT ["/bin/bash", "-c", "cd /workspace/work && exec \"$@\"", "--"]
CMD ["sleep", "infinity"]
