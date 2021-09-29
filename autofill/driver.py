import os
import time
from concurrent.futures import ThreadPoolExecutor

import attr
import constants
import enlighten
from constants import Faces, States
from order import CardImage, CardImageCollection, CardOrder
from selenium import webdriver
from selenium.common import exceptions as sl_exc
from selenium.common.exceptions import (NoAlertPresentException,
                                        NoSuchElementException,
                                        UnexpectedAlertPresentException)
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.expected_conditions import \
    invisibility_of_element
from selenium.webdriver.support.ui import Select, WebDriverWait
from utils import TEXT_BOLD, TEXT_END, InvalidStateException, alert_handler
from webdriver_manager.chrome import ChromeDriverManager

# Disable logging messages for webdriver_manager
os.environ["WDM_LOG_LEVEL"] = "0"


def initialise_driver() -> webdriver.Chrome:
    chrome_options = Options()
    chrome_options.add_argument("--log-level=3")
    chrome_options.add_experimental_option("excludeSwitches", ["enable-logging"])
    chrome_options.add_experimental_option("detach", True)
    driver = webdriver.Chrome(ChromeDriverManager().install(), options=chrome_options)
    driver.set_window_size(1200, 900)
    driver.implicitly_wait(5)
    driver.set_network_conditions(offline=False, latency=5, throughput=5 * 125000)

    return driver


# TODO: wrapper for dismissing alerts on MPC's website (can occur if the user clicks)
# TODO: wrapper for uncaught exceptions - display the error message and prompt to press any key before closing
@attr.s
class AutofillDriver:
    driver: webdriver.Chrome = attr.ib(default=attr.Factory(initialise_driver))
    starting_url: str = attr.ib(
        init=False,
        default="https://www.makeplayingcards.com/design/custom-blank-card.html",
    )
    order: CardOrder = attr.ib(default=attr.Factory(CardOrder.from_xml_in_folder))
    state: str = attr.ib(init=False, default=States.initialising)
    action: str = attr.ib(init=False, default="")
    manager: enlighten.Manager = attr.ib(
        init=False, default=attr.Factory(enlighten.get_manager)
    )
    status_bar: enlighten.StatusBar = attr.ib(init=False, default=False)
    download_bar: enlighten.Counter = attr.ib(init=False, default=None)
    upload_bar: enlighten.Counter = attr.ib(init=False, default=None)

    # region initialisation
    def configure_bars(self) -> None:
        num_images = len(self.order.fronts.cards) + len(self.order.backs.cards)
        status_format = "State: {state}, Action: {action}"
        self.status_bar = self.manager.status_bar(
            status_format=status_format,
            state=f"{TEXT_BOLD}{self.state}{TEXT_END}",
            action=f"{TEXT_BOLD}N/A{TEXT_END}",
            position=1,
        )
        self.download_bar = self.manager.counter(
            total=num_images, desc="Images Downloaded", position=2
        )
        self.upload_bar = self.manager.counter(
            total=num_images, desc="Images Uploaded", position=3
        )

        self.status_bar.refresh()
        self.download_bar.refresh()
        self.upload_bar.refresh()

    def __attrs_post_init__(self):
        self.configure_bars()
        self.driver.get(self.starting_url)
        self.set_state(States.defining_order)

    # endregion

    # region public
    # Methods in this region should begin by asserting that the driver is in the state they expect, then update
    # the driver's state with wherever the program ends up after the method's logic runs.

    def define_order(self) -> None:
        # TODO: skip this step if resuming
        self.assert_state(States.defining_order)
        # Select card stock
        stock_dropdown = Select(self.driver.find_element_by_id("dro_paper_type"))
        stock_dropdown.select_by_visible_text(self.order.details.stock)

        # Select number of cards
        qty_dropdown = Select(self.driver.find_element_by_id("dro_choosesize"))
        qty_dropdown.select_by_value(str(self.order.details.bracket))

        # Switch the finish to foil if the user ordered foil cards
        if self.order.details.foil:
            foil_dropdown = Select(self.driver.find_element_by_id("dro_product_effect"))
            foil_dropdown.select_by_value("EF_055")

        self.set_state(States.paging_to_fronts)

    def insert_fronts(self) -> None:
        self.page_to_fronts()

        self.assert_state(States.inserting_fronts)
        self.upload_and_insert_images(self.order.fronts)

        self.set_state(States.paging_to_backs)

    def insert_backs(self) -> None:
        self.page_to_backs()

        self.assert_state(States.inserting_backs)
        self.upload_and_insert_images(self.order.backs)

        self.set_state(States.paging_to_review)

    # endregion

    # region helpers

    def set_state(self, state: str, action: str = "") -> None:
        self.state = state
        self.action = action
        self.status_bar.update(
            state=f"{TEXT_BOLD}{self.state}{TEXT_END}",  # type: ignore
            action=f"{TEXT_BOLD}{self.action or 'N/A'}{TEXT_END}",  # type: ignore
        )
        self.status_bar.refresh()

    def assert_state(self, expected_state) -> None:
        if self.state != expected_state:
            raise InvalidStateException(expected_state, self.state)
            # TODO: how do we recover from here?

    @alert_handler
    def switch_to_frame(self, frame: str) -> None:
        try:
            self.driver.switch_to.frame(frame)
        except (sl_exc.NoSuchFrameException, sl_exc.NoSuchElementException):
            pass

    @alert_handler
    def wait(self) -> None:
        """
        Wait until the loading circle in MPC disappears
        """

        try:
            wait_elem = self.driver.find_element_by_id("sysdiv_wait")
            # Wait for the element to become invisible
            while True:
                try:
                    WebDriverWait(self.driver, 100).until(
                        invisibility_of_element(wait_elem)
                    )
                except sl_exc.TimeoutException:
                    continue
                break
        except sl_exc.NoSuchElementException:
            return

    @alert_handler
    def execute_javascript(self, js: str) -> None:
        """
        Executes the given JavaScript command in self.driver
        This can occasionally fail - e.g.
        "selenium.common.exceptions.JavaScriptException: Message: javascript error: setMode is not defined"
        """
        # TODO: handle javascript errors

        self.driver.execute_script(f"javascript:{js}")

    def next_step(self) -> None:
        """
        Page through to the next step in MPC.
        """

        self.wait()
        self.execute_javascript("oDesign.setNextStep();")

    def different_images(self) -> None:
        """
        Sets each card in the current face to use different images.
        """
        self.execute_javascript("setMode('ImageText', 0);")

    def same_images(self) -> None:
        """
        Sets each card in the current face to use the same image.
        """
        self.execute_javascript("setMode('ImageText', 1);")

    def page_to_fronts(self) -> None:
        self.assert_state(States.paging_to_fronts)

        # Accept current settings and move to next step
        self.execute_javascript(
            "doPersonalize('https://www.makeplayingcards.com/products/pro_item_process_flow.aspx');"
        )

        # Set the desired number of cards, then move to the next step
        self.switch_to_frame("sysifm_loginFrame")
        self.execute_javascript(
            f"document.getElementById('txt_card_number').value={self.order.details.quantity};"
        )
        self.different_images()
        self.driver.switch_to.default_content()

        self.set_state(States.inserting_fronts)

    def page_to_backs(self) -> None:
        self.assert_state(States.paging_to_backs)

        self.next_step()
        self.wait()
        try:
            self.driver.find_element_by_id("closeBtn").click()
        except NoSuchElementException:
            pass
        self.next_step()

        self.switch_to_frame("sysifm_loginFrame")
        if len(self.order.backs.cards) == 1:
            # Same cardback for every card
            self.same_images()
        else:
            # Different cardbacks
            self.different_images()
        self.driver.switch_to.default_content()

        self.set_state(States.inserting_backs)

    # endregion

    # region uploading
    def upload_image(self, image: "CardImage") -> str:
        """
        Uploads the given CardImage. Returns the image's PID in MPC.
        """

        # TODO: needs overhauling

        if image.file_exists():
            self.set_state(self.state, f'Uploading "{image.name}"')
            num_elems = len(
                self.driver.find_elements_by_xpath("//*[contains(@id, 'upload_')]")
            )

            # if an image is uploading already, wait for it to finish uploading before continuing
            progress_container = self.driver.find_element_by_id(
                "divFileProgressContainer"
            )

            while progress_container.value_of_css_property("display") != "none":
                time.sleep(3)

            while progress_container.value_of_css_property("display") == "none":
                # Attempt to upload card until the upload progress bar appears
                self.driver.find_element_by_xpath('//*[@id="uploadId"]').send_keys(
                    image.file_path
                )
                time.sleep(1)
                progress_container = self.driver.find_element_by_id(
                    "divFileProgressContainer"
                )

            # Wait as long as necessary for the image to finish uploading
            while True:
                try:
                    # Wait until the image has finished uploading
                    elem = self.driver.find_elements_by_xpath(
                        "//*[contains(@id, 'upload_')]"
                    )
                    if len(elem) > num_elems:
                        # Return the uploaded card's PID so we can easily insert it into slots
                        return elem[-1].get_attribute("pid")

                    time.sleep(2)

                except sl_exc.UnexpectedAlertPresentException:
                    # If the user clicks on the window, alerts can pop up - we just want to dismiss these and move on
                    try:
                        alert = self.driver.switch_to.alert
                        alert.accept()
                    except sl_exc.NoAlertPresentException:
                        pass
        else:
            pass  # error queue

        self.set_state(self.state)

    def insert_image(self, pid: str, image: CardImage):
        # TODO: needs better safeguarding against errors, and validation that the image was uploaded
        if pid:
            self.set_state(self.state, f'Inserting "{image.name}"')
            self.execute_javascript("l = PageLayout.prototype")
            for slot in image.slots:
                # Insert the card into each slot and wait for the page to load before continuing
                self.execute_javascript(
                    f'l.applyDragPhoto(l.getElement3("dnImg", {slot}), 0, "{pid}")'
                )
                self.wait()
            self.set_state(self.state)

    def upload_and_insert_image(self, image: "CardImage") -> None:
        # validate that the current order face matches this image's face
        # wait until the image has been downloaded

        pid = self.upload_image(image)
        self.insert_image(pid, image)

    def upload_and_insert_images(self, images: "CardImageCollection") -> None:
        for _ in range(len(images.cards)):
            image: "CardImage" = images.queue.get()
            if image.downloaded:
                self.upload_and_insert_image(image)
            self.upload_bar.update()

    # endregion

    def execute(self) -> None:
        # TODO: delay initialisation of download bars to here?
        print("Starting image downloader and webdriver processes.")
        with ThreadPoolExecutor(max_workers=constants.THREADS) as pool:
            self.order.fronts.download_images(pool, self.download_bar)
            self.order.backs.download_images(pool, self.download_bar)

            self.define_order()
            self.insert_fronts()
            self.insert_backs()
