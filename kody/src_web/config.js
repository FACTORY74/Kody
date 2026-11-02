/*
 * Kody — General-Purpose Mapping Application
 *
 * A Python-based mapping application with a JavaScript/HTML web interface and a plugin architecture for extending functionality.
 *
 * Copyright (c) 2026 Factory74
 *
 * SPDX-License-Identifier: MIT
 *
 * Distributed under the MIT License. See LICENSE for details.
 * Provided "AS IS", without warranty of any kind.
 */

const form = document.querySelector("#services-form");
const serviceList = document.querySelector("#service-list");
const template = document.querySelector("#service-template");
const formStatus = document.querySelector("#form-status");
const configurationServiceUrl = "http://127.0.0.1:8001";
const dataDirectoryForm = document.querySelector("#data-directory-form");
const dataDirectory = document.querySelector("#data-directory");
let servicesRevision = null;
let dataDirectoryRevision = null;

function addService(service = {}) {
  const element = template.content.cloneNode(true);
  const serviceCard = element.querySelector(".service");
  const isManaged = service.managed === true;
  serviceCard.querySelector('[name="id"]').value = service.id || "";
  serviceCard.querySelector('[name="type"]').value = service.type || "vecmap";
  serviceCard.querySelector('[name="url"]').value = service.url || "";
  if (isManaged) {
    serviceCard.dataset.managed = "true";
    serviceCard.querySelector(".service-card-title").textContent = "Local service managed by Kody runtime";
    serviceCard.querySelectorAll("input, select, button").forEach((control) => {
      control.disabled = true;
    });
  } else {
    serviceCard.querySelector("[data-remove-service]").addEventListener("click", () => serviceCard.remove());
  }
  serviceList.append(element);
}

async function loadServices() {
  const response = await fetch(`${configurationServiceUrl}/api/config/services`);
  if (!response.ok) {
    throw new Error(`configuration returned ${response.status}`);
  }
  servicesRevision = response.headers.get("ETag");
  const configuration = await response.json();
  configuration.services.forEach(addService);
}

async function loadDataDirectory() {
  const response = await fetch(`${configurationServiceUrl}/api/config/data-directory`);
  if (!response.ok) throw new Error(`configuration returned ${response.status}`);
  dataDirectoryRevision = response.headers.get("ETag");
  dataDirectory.value = (await response.json()).dataDirectory;
}

dataDirectoryForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    const response = await fetch(`${configurationServiceUrl}/api/config/data-directory`, {
      method: "PUT",
      headers: {
        "Content-Type": "application/json",
        ...(dataDirectoryRevision ? { "If-Match": dataDirectoryRevision } : {}),
      },
      body: JSON.stringify({ dataDirectory: dataDirectory.value }),
    });
    if (!response.ok) throw new Error(`configuration returned ${response.status}`);
    dataDirectoryRevision = response.headers.get("ETag");
    formStatus.textContent = "Data folder saved. Restart Kody services to apply it.";
  } catch (error) {
    formStatus.textContent = `Unable to save data folder: ${error.message}`;
  }
});

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    const services = [...serviceList.querySelectorAll(".service")]
      .filter((serviceCard) => serviceCard.dataset.managed !== "true")
      .map((serviceCard) => ({
        id: serviceCard.querySelector('[name="id"]').value,
        type: serviceCard.querySelector('[name="type"]').value,
        url: serviceCard.querySelector('[name="url"]').value,
      }));
    const response = await fetch(`${configurationServiceUrl}/api/config/services`, {
      method: "PUT",
      headers: {
        "Content-Type": "application/json",
        ...(servicesRevision ? { "If-Match": servicesRevision } : {}),
      },
      body: JSON.stringify({ services }),
    });
    if (!response.ok) {
      const error = await response.json();
      throw new Error(error.detail?.[0]?.msg || "the configuration was rejected");
    }
    servicesRevision = response.headers.get("ETag");
    formStatus.textContent = "External services saved to config/kody.json.";
  } catch (error) {
    formStatus.textContent = `Unable to save services: ${error.message}`;
  }
});

document.querySelector("#add-service").addEventListener("click", () => addService());
loadServices().catch((error) => {
  formStatus.textContent = `Unable to load services: ${error.message}`;
});
loadDataDirectory().catch((error) => {
  formStatus.textContent = `Unable to load data folder: ${error.message}`;
});
